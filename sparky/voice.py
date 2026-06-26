"""
Módulo de voz de Sparky — Piper CALIENTE con cola de oraciones y barge-in.

Clave de latencia:
    Antes se lanzaba un piper.exe NUEVO por frase → recargaba el modelo de
    63 MB cada vez (~2 s por frase). Ahora se mantiene UN proceso Piper vivo
    (--json-input) que carga el modelo una sola vez; cada frase tarda ~0.3 s.

Arquitectura (pipeline en 2 etapas para solapar síntesis y reproducción):
    1. queue_sentence(s): escribe la frase al stdin de Piper (síntesis empieza
       YA, async) y encola el WAV de salida.
    2. _player_worker: reproduce los WAV en orden según se van completando.
    Así Piper sintetiza la frase 2,3,... mientras suena la frase 1.

Barge-in:
    stop_speaking() para el audio, descarta lo pendiente y RECREA el proceso
    Piper (para limpiar la cola interna de síntesis). El recreado ocurre en
    segundo plano: su coste se solapa con que el usuario habla + STT + LLM.
"""

import os
import io
import glob
import json
import time
import wave
import random
import subprocess
import threading
import queue
import numpy as np
import sounddevice as sd
from rich import print as rprint
from sparky.config import (
    VOICE_ENABLED, VOICE_RATE, PIPER_EXE, PIPER_MODEL,
    PIPER_SENTENCE_SILENCE, PIPER_TMP, FILLER_ENABLED, FILLER_PHRASES, AVATAR_3D,
    TTS_ENGINE,
    AZURE_SPEECH_REGION, AZURE_VOICE, AZURE_SPEECH_KEY,
)

# Visemas de Microsoft (0-21) → visemas Oculus que usa TalkingHead
_MS2OCU = {
    0: "sil", 1: "aa", 2: "aa", 3: "O", 4: "E", 5: "E", 6: "I", 7: "U",
    8: "O", 9: "O", 10: "O", 11: "aa", 12: "kk", 13: "RR", 14: "nn",
    15: "SS", 16: "CH", 17: "TH", 18: "FF", 19: "DD", 20: "kk", 21: "PP",
}


class SparkyVoice:
    """Voz con proceso Piper persistente, cola de oraciones y barge-in."""

    def __init__(self, enabled=VOICE_ENABLED):
        self.enabled = enabled
        self.engine_name = "none"

        self._play_queue = queue.Queue()      # rutas de WAV a reproducir en orden
        self._is_speaking = threading.Event()
        self._stop_flag = threading.Event()   # Señal para parar la reproducción
        self.interrupted = threading.Event()  # Señal para que brain pare de generar
        self._worker_thread = None

        # Proceso Piper persistente
        self._proc = None
        self._proc_lock = threading.Lock()
        self._counter = 0                     # nombres de WAV únicos por sesión
        self._fillers = []                    # WAVs de muletillas pregeneradas

        # Modo 3D: el audio se reproduce en el navegador (para lip-sync HeadAudio)
        self._avatar = None
        self._speech_end = 0.0                # instante estimado de fin de habla (modo navegador)

        # Azure Speech: voz humana + visemas (labios exactos). Si falla, cae a Piper.
        self._azure = None                    # SpeechSynthesizer (lazy)
        self._use_azure = (TTS_ENGINE == "azure")

        if PIPER_EXE.exists() and PIPER_MODEL.exists():
            self.engine_name = "piper"
            PIPER_TMP.mkdir(exist_ok=True)
            self._clean_tmp()
            rprint("[dim]Voz: Piper TTS (neural, proceso caliente)[/dim]")
            self._ensure_piper()  # precargar el modelo al arrancar
            if not self._use_azure:           # con Azure NO usamos fillers (voz Piper vieja)
                self._make_fillers()
        else:
            self.engine_name = "pyttsx3"
            rprint("[dim]Voz: pyttsx3 (fallback)[/dim]")

    @property
    def is_speaking(self):
        return self._is_speaking.is_set()

    def set_avatar(self, avatar):
        """Conecta el avatar para enviarle el audio (modo 3D en navegador)."""
        self._avatar = avatar

    @property
    def _to_browser(self):
        return AVATAR_3D and self._avatar is not None and self.engine_name == "piper"

    # ── Proceso Piper persistente ────────────────────────────

    def _spawn_piper(self):
        """Crea (o recrea) el proceso Piper. El modelo se carga aquí, una vez."""
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = subprocess.Popen(
            [
                str(PIPER_EXE), "--model", str(PIPER_MODEL),
                "--json-input", "-q",
                "--sentence_silence", str(PIPER_SENTENCE_SILENCE),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _ensure_piper(self):
        """Garantiza que hay un proceso Piper vivo."""
        with self._proc_lock:
            if self._proc is None or self._proc.poll() is not None:
                self._spawn_piper()

    def _synth(self, text):
        """Pide a Piper sintetizar una frase. Devuelve la ruta del WAV (aún en curso)."""
        self._ensure_piper()
        self._counter += 1
        out = os.path.abspath(str(PIPER_TMP / f"s{self._counter}.wav"))
        line = (json.dumps({"text": text, "output_file": out}) + "\n").encode("utf-8")
        with self._proc_lock:
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._spawn_piper()
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
        return out

    def _wait_ready(self, path, timeout=15.0):
        """Espera a que el WAV esté completo (tamaño estable). Aborta si stop."""
        last = -1
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_flag.is_set():
                return False
            if os.path.exists(path):
                sz = os.path.getsize(path)
                if sz > 44 and sz == last:
                    return True
                last = sz
            time.sleep(0.01)
        return os.path.exists(path) and os.path.getsize(path) > 44

    def _play_file(self, path):
        """Reproduce un WAV (bloquea). sd.stop() desde otro hilo lo interrumpe."""
        if self._stop_flag.is_set():
            return
        try:
            with wave.open(path, "rb") as wf:
                rate = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            if not frames or self._stop_flag.is_set():
                return
            audio = np.frombuffer(frames, dtype=np.int16)
            sd.play(audio, samplerate=rate)
            sd.wait()
        except Exception as e:
            if not self._stop_flag.is_set():
                rprint(f"[dim red]Error reproduciendo: {e}[/dim red]")

    def _wav_duration(self, path):
        try:
            with wave.open(path, "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            return 1.5

    def _emit(self, path):
        """Saca una frase ya sintetizada: al navegador (3D) o por altavoz (2D)."""
        if self._stop_flag.is_set():
            return
        if self._to_browser:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                return
            self._avatar.push_audio(data)
            # estimar cuándo terminará de sonar (los clips suenan en orden)
            self._speech_end = max(self._speech_end, time.time()) + self._wav_duration(path)
        else:
            self._play_file(path)

    # ── Azure Speech (voz humana + visemas) ──────────────────

    def _azure_synth(self):
        if self._azure is None:
            import azure.cognitiveservices.speech as speechsdk
            cfg = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
            cfg.speech_synthesis_voice_name = AZURE_VOICE
            cfg.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Riff22050Hz16BitMonoPcm)
            synth = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
            self._azure_visemes = []
            synth.viseme_received.connect(
                lambda evt: self._azure_visemes.append((evt.audio_offset / 10000.0, evt.viseme_id)))
            self._azure = synth
        return self._azure

    def _synth_azure(self, text):
        """Sintetiza con Azure. Devuelve (wav_bytes, {visemes,vtimes,vdurations}) o (None,None)."""
        try:
            import azure.cognitiveservices.speech as speechsdk
            synth = self._azure_synth()
            self._azure_visemes = []  # reiniciar por frase
            result = synth.speak_text_async(text).get()
            if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                rprint(f"[dim yellow]Azure TTS no completó ({result.reason}); usando Piper[/dim yellow]")
                self._use_azure = False
                return None, None
            vis = sorted(self._azure_visemes)
            visemes = [_MS2OCU.get(vid, "sil") for (_t, vid) in vis]
            vtimes = [t for (t, _vid) in vis]
            vdur = [(vtimes[i + 1] - vtimes[i]) if i + 1 < len(vtimes) else 120
                    for i in range(len(vtimes))]
            return result.audio_data, {"visemes": visemes, "vtimes": vtimes, "vdurations": vdur}
        except Exception as e:
            rprint(f"[dim yellow]Azure error ({e}); usando Piper[/dim yellow]")
            self._use_azure = False
            return None, None

    def _emit_bytes(self, wav_bytes, visemes=None):
        """Saca un WAV en memoria (con visemas opcionales): al navegador o al altavoz."""
        if self._stop_flag.is_set() or not wav_bytes:
            return
        if self._to_browser:
            self._avatar.push_audio(wav_bytes, visemes)
            self._speech_end = max(self._speech_end, time.time()) + self._wav_dur_bytes(wav_bytes)
        else:
            try:
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    rate = wf.getframerate()
                    frames = wf.readframes(wf.getnframes())
                sd.play(np.frombuffer(frames, dtype=np.int16), samplerate=rate)
                sd.wait()
            except Exception:
                pass

    def _wav_dur_bytes(self, wav_bytes):
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            return 1.5

    def _clean_tmp(self):
        for f in glob.glob(str(PIPER_TMP / "*.wav")):
            try:
                os.remove(f)
            except OSError:
                pass

    def _make_fillers(self):
        """Pregenera WAVs de muletillas (una vez, al arrancar)."""
        if not FILLER_ENABLED:
            return
        for i, phrase in enumerate(FILLER_PHRASES):
            out = os.path.abspath(str(PIPER_TMP / f"filler_{i}.wav"))
            line = (json.dumps({"text": phrase, "output_file": out}) + "\n").encode("utf-8")
            with self._proc_lock:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            if self._wait_ready(out):
                self._fillers.append(out)

    def play_filler(self):
        """Encola una muletilla pregenerada (suena al instante mientras el LLM piensa)."""
        if self._fillers and not self.interrupted.is_set():
            self._play_queue.put(random.choice(self._fillers))

    # ── API pública ──────────────────────────────────────────

    def speak_now(self, text):
        """Habla un texto inmediatamente (bloquea). Para saludos/avisos."""
        if not text.strip():
            return
        rprint(f"\n[bold cyan]Sparky:[/bold cyan] {text}")
        if not self.enabled:
            return
        self._is_speaking.set()
        self._stop_flag.clear()
        self._speech_end = 0.0
        try:
            if self._use_azure:
                wav, vis = self._synth_azure(text)
                if wav:
                    self._emit_bytes(wav, vis)
                    self._wait_browser_done()
                elif self.engine_name == "piper":      # Azure falló → Piper
                    path = self._synth(text)
                    if self._wait_ready(path):
                        self._emit(path)
                        self._wait_browser_done()
            elif self.engine_name == "piper":
                path = self._synth(text)
                if self._wait_ready(path):
                    self._emit(path)
                    self._wait_browser_done()  # solo bloquea en modo navegador
            else:
                self._pyttsx3_speak(text)
        finally:
            self._is_speaking.clear()

    def _wait_browser_done(self):
        """Espera a que el navegador realmente TERMINE de reproducir (no por
        estimación). Así is_speaking sigue True mientras suena → barge-in real."""
        if not self._to_browser:
            return
        # 1) esperar a que EMPIECE a sonar (hasta 4s por la latencia de descarga)
        t0 = time.time()
        while not self._stop_flag.is_set() and not self._avatar.browser_busy() and time.time() - t0 < 4:
            time.sleep(0.05)
        # 2) esperar a que DEJE de sonar
        while not self._stop_flag.is_set() and self._avatar.browser_busy():
            time.sleep(0.05)

    def start_speaking(self):
        """Inicia un turno de habla: limpia estado y arranca el player worker."""
        # Vaciar cola de reproducción de turnos anteriores
        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except queue.Empty:
                break

        self._stop_flag.clear()
        self.interrupted.clear()
        self._is_speaking.set()
        self._speech_end = 0.0
        if self.engine_name == "piper":
            self._ensure_piper()
        self._worker_thread = threading.Thread(target=self._player_worker, daemon=True)
        self._worker_thread.start()

    def queue_sentence(self, sentence):
        """Encola una oración: inicia su síntesis YA y la pone en cola de reproducción."""
        if self.interrupted.is_set() or not sentence.strip():
            return
        s = sentence.strip()
        if self._use_azure:
            self._play_queue.put(("azure", s))         # se sintetiza en el worker
        elif self.engine_name == "piper":
            self._play_queue.put(self._synth(s))       # ruta del WAV (síntesis ya iniciada)
        else:
            self._play_queue.put(("pyttsx3", s))

    def finish_speaking(self):
        """Señala fin de oraciones y espera a que termine la reproducción."""
        if self.interrupted.is_set():
            self._is_speaking.clear()
            return
        self._play_queue.put(None)
        if self._worker_thread:
            self._worker_thread.join(timeout=60)
        # En navegador el worker solo ENVÍA los clips; esperar a que suenen
        self._wait_browser_done()
        self._is_speaking.clear()

    def stop_speaking(self):
        """Para la voz INMEDIATAMENTE (barge-in)."""
        self._stop_flag.set()
        self.interrupted.set()

        while not self._play_queue.empty():
            try:
                self._play_queue.get_nowait()
            except queue.Empty:
                break
        self._play_queue.put(None)

        try:
            sd.stop()
        except Exception:
            pass

        # Navegador: cancelar lo pendiente y avisar (sube gen → el avatar se calla)
        if self._to_browser:
            self._avatar.cancel_audio()
        self._speech_end = 0.0

        if self._worker_thread:
            self._worker_thread.join(timeout=3)

        # Recrear Piper en segundo plano para limpiar su cola interna de síntesis.
        # El coste se solapa con que el usuario habla + STT + LLM.
        if self.engine_name == "piper":
            threading.Thread(target=self._ensure_respawn, daemon=True).start()

        self._is_speaking.clear()

    def _ensure_respawn(self):
        with self._proc_lock:
            self._spawn_piper()

    # ── Worker de reproducción ───────────────────────────────

    def _player_worker(self):
        """Reproduce los WAV en orden conforme se completan."""
        while not self._stop_flag.is_set():
            try:
                item = self._play_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            if self._stop_flag.is_set():
                break
            try:
                if isinstance(item, tuple):
                    if item[0] == "azure":
                        wav, vis = self._synth_azure(item[1])
                        if wav:
                            self._emit_bytes(wav, vis)
                        else:                          # Azure falló → Piper
                            path = self._synth(item[1])
                            if self._wait_ready(path):
                                self._emit(path)
                    else:                              # fallback pyttsx3
                        self._pyttsx3_speak(item[1])
                else:
                    if self._wait_ready(item):
                        self._emit(item)
            except Exception as e:
                rprint(f"[dim red]Error TTS: {e}[/dim red]")
        # En modo navegador el worker solo ENCOLA: Chrome sigue sonando después.
        # Marcar fin aquí dejaría is_speaking=False durante la reproducción y el
        # barge-in moriría (el callback ve "no está hablando"). finish_speaking/
        # stop_speaking lo limpian tras esperar al navegador.
        if not self._to_browser:
            self._is_speaking.clear()

    # ── Fallback pyttsx3 ─────────────────────────────────────

    def _pyttsx3_speak(self, text):
        if self._stop_flag.is_set():
            return
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", VOICE_RATE)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            del engine
        except Exception as e:
            if not self._stop_flag.is_set():
                rprint(f"[dim red]Error pyttsx3: {e}[/dim red]")

    def shutdown(self):
        """Cierra el proceso Piper limpiamente."""
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
