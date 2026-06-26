"""
Cerebro de Sparky — Híbrido (Nube + Local) + Emociones + Streaming por oraciones.

Durante el streaming del LLM, detecta oraciones completas y las envía
al módulo de voz para que Sparky hable mientras sigue pensando.
"""

import time
import json
import re
import requests
from ollama import chat
from rich import print as rprint
from sparky.config import (
    LOCAL_MODEL, LOCAL_OPTIONS, LOCAL_ENABLED, MAX_HISTORY_MESSAGES,
    CLOUD_ENABLED, CLOUD_API_URL, CLOUD_API_KEY,
    CLOUD_MODEL, CLOUD_MAX_TOKENS, CLOUD_TEMPERATURE, CLOUD_TIMEOUT, CLOUD_EXTRA,
)
from sparky.emotions import parse_emotion, format_emotion, get_emotion_color

# Patrón para detectar fin de oración
SENTENCE_END = re.compile(r'[.!?;:]\s*$')


class SparkyBrain:
    """Maneja la comunicación con el modelo de IA."""

    def __init__(self):
        self.conversation = []
        self.last_engine = None
        self.last_emotion = "neutral"
        self._emotion_set = False

    def _detect_emotion_live(self, full_response):
        """Fija last_emotion en cuanto aparece la etiqueta [emoción] al inicio.
        Así el avatar reacciona durante el habla, no un turno después.
        """
        if self._emotion_set:
            return
        stripped = full_response.lstrip()
        if stripped.startswith("[") and "]" in stripped:
            emotion, _ = parse_emotion(full_response)
            self.last_emotion = emotion
            self._emotion_set = True

    def think(self, user_text, system_prompt, voice=None):
        """Genera respuesta con streaming y envía oraciones a voice.

        Args:
            user_text: Texto del usuario.
            system_prompt: Prompt de sistema.
            voice: SparkyVoice instance (si se pasa, envía oraciones para TTS).

        Returns:
            tuple: (texto_limpio, segundos)
        """
        messages = [{"role": "system", "content": system_prompt}]
        recent = self.conversation[-(MAX_HISTORY_MESSAGES * 2):]
        messages.extend(recent)
        messages.append({"role": "user", "content": user_text})

        self._emotion_set = False  # para detectar la emoción en vivo (avatar)

        raw_response = None
        elapsed = 0

        # Iniciar TTS worker si voice está disponible
        if voice and voice.enabled:
            voice.start_speaking()
            voice.play_filler()  # muletilla instantánea mientras llega el 1er token

        # 1) Nube con streaming (baja latencia)
        if CLOUD_ENABLED:
            raw_response, elapsed = self._think_cloud(messages, voice)
            # 2) Si el streaming falla, reintentar SIN streaming (más robusto, todo API)
            if raw_response is None:
                raw_response, elapsed = self._think_cloud_nonstream(messages, voice)

        # 3) Fallback local (Ollama) solo si está habilitado — por defecto NO
        if raw_response is None and LOCAL_ENABLED:
            raw_response, elapsed = self._think_local(messages, voice)

        # Señalar fin de oraciones al TTS
        if voice and voice.enabled:
            voice.finish_speaking()

        if raw_response is None:
            return None, 0

        # Parsear emoción
        emotion, clean_text = parse_emotion(raw_response)
        self.last_emotion = emotion

        color = get_emotion_color(emotion)
        rprint(f"[{color}]{format_emotion(emotion)}[/{color}]")

        # Guardar en historial
        self.conversation.append({"role": "user", "content": user_text})
        self.conversation.append({"role": "assistant", "content": clean_text})

        return clean_text, elapsed

    def _send_sentences(self, full_text, voice):
        """Extrae y envía oraciones completas al TTS.

        Retorna el texto restante que aún no forma una oración completa.
        """
        if not voice or not voice.enabled:
            return full_text

        # Buscar la última oración completa
        sentences = re.split(r'(?<=[.!?;:])\s+', full_text)

        if len(sentences) > 1:
            # Hay al menos una oración completa — enviar todas menos la última
            for s in sentences[:-1]:
                # No enviar la etiqueta de emoción como voz
                clean = re.sub(r'^\[(?:neutral|happy|thinking|sad|alert|sleep)\]\s*', '', s)
                if clean.strip():
                    voice.queue_sentence(clean)
            return sentences[-1]  # Retornar fragmento incompleto

        return full_text

    # ── Motor: NVIDIA Cloud API ──────────────────────────────

    def _think_cloud(self, messages, voice=None):
        """Envía a NVIDIA API con streaming SSE."""
        start = time.time()

        headers = {
            "Authorization": f"Bearer {CLOUD_API_KEY}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        payload = {
            "model": CLOUD_MODEL,
            "messages": messages,
            "max_tokens": CLOUD_MAX_TOKENS,
            "temperature": CLOUD_TEMPERATURE,
            "top_p": 0.95,
            "stream": True,
            **CLOUD_EXTRA,   # apaga el razonamiento en modelos Nemotron
        }

        try:
            rprint("[dim][ nube ][/dim] ", end="")
            rprint("[bold cyan]Sparky:[/bold cyan] ", end="")

            resp = requests.post(
                CLOUD_API_URL, headers=headers, json=payload,
                stream=True, timeout=CLOUD_TIMEOUT,
            )

            if resp.status_code != 200:
                rprint(f"\n[dim yellow]Nube HTTP {resp.status_code}, usando local...[/dim yellow]")
                return None, 0

            full_response = ""
            buffer = ""  # Buffer para detectar oraciones

            for line in resp.iter_lines():
                # Barge-in: parar de generar si el usuario interrumpió
                if voice and voice.interrupted.is_set():
                    break

                if not line:
                    continue
                line_str = line.decode("utf-8")
                if not line_str.startswith("data: "):
                    continue
                data = line_str[6:]
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content", "")
                    if content:
                        print(content, end="", flush=True)
                        full_response += content
                        self._detect_emotion_live(full_response)
                        buffer += content
                        buffer = self._send_sentences(buffer, voice)
                except json.JSONDecodeError:
                    continue

            # Enviar texto restante en el buffer
            if buffer.strip() and voice and voice.enabled:
                clean = re.sub(r'^\[(?:neutral|happy|thinking|sad|alert|sleep)\]\s*', '', buffer)
                if clean.strip():
                    voice.queue_sentence(clean.strip())

            print()
            elapsed = round(time.time() - start, 2)
            self.last_engine = "nube"
            return full_response, elapsed

        except requests.exceptions.Timeout:
            rprint(f"\n[dim yellow]Nube: timeout ({CLOUD_TIMEOUT}s), usando local...[/dim yellow]")
            return None, 0
        except requests.exceptions.ConnectionError:
            rprint(f"\n[dim yellow]Sin internet, usando local...[/dim yellow]")
            return None, 0
        except Exception as e:
            rprint(f"\n[dim yellow]Error nube: {e}, reintento sin streaming...[/dim yellow]")
            return None, 0

    # ── Motor: NVIDIA Cloud SIN streaming (respaldo robusto) ──

    def _think_cloud_nonstream(self, messages, voice=None):
        """Respaldo cuando el streaming SSE falla. Pide la respuesta completa
        (probado que funciona) y la envía a la voz por oraciones."""
        start = time.time()
        try:
            rprint("[dim][ nube-2 ][/dim] ", end="")
            resp = requests.post(
                CLOUD_API_URL,
                headers={"Authorization": f"Bearer {CLOUD_API_KEY}",
                         "Accept": "application/json"},
                json={"model": CLOUD_MODEL, "messages": messages,
                      "max_tokens": CLOUD_MAX_TOKENS, "temperature": CLOUD_TEMPERATURE,
                      "top_p": 0.95, **CLOUD_EXTRA},
                timeout=CLOUD_TIMEOUT,
            )
            if resp.status_code != 200:
                rprint(f"[dim yellow]Nube-2 HTTP {resp.status_code}: {resp.text[:150]}[/dim yellow]")
                return None, 0
            text = resp.json()["choices"][0]["message"]["content"]
            rprint(f"[bold cyan]Sparky:[/bold cyan] {text}")
            self._detect_emotion_live(text)
            if voice and voice.enabled:
                for s in re.split(r'(?<=[.!?;:])\s+', text):
                    clean = re.sub(r'^\[(?:neutral|happy|thinking|sad|alert|sleep)\]\s*', '', s)
                    if clean.strip():
                        voice.queue_sentence(clean.strip())
            self.last_engine = "nube-2"
            return text, round(time.time() - start, 2)
        except Exception as e:
            rprint(f"[dim yellow]Nube-2 error: {e}[/dim yellow]")
            return None, 0

    # ── Motor: Ollama Local ──────────────────────────────────

    def _think_local(self, messages, voice=None):
        """Envía a Ollama local con streaming."""
        start = time.time()

        try:
            rprint("[dim][ local ][/dim] ", end="")
            rprint("[bold cyan]Sparky:[/bold cyan] ", end="")

            stream = chat(
                model=LOCAL_MODEL, messages=messages,
                stream=True, options=LOCAL_OPTIONS,
            )

            full_response = ""
            buffer = ""

            for chunk in stream:
                # Barge-in: parar de generar si el usuario interrumpió
                if voice and voice.interrupted.is_set():
                    break

                token = chunk["message"]["content"]
                print(token, end="", flush=True)
                full_response += token
                self._detect_emotion_live(full_response)
                buffer += token
                buffer = self._send_sentences(buffer, voice)

            # Enviar texto restante
            if buffer.strip() and voice and voice.enabled:
                clean = re.sub(r'^\[(?:neutral|happy|thinking|sad|alert|sleep)\]\s*', '', buffer)
                if clean.strip():
                    voice.queue_sentence(clean.strip())

            print()
            elapsed = round(time.time() - start, 2)
            self.last_engine = "local"
            return full_response, elapsed

        except Exception as e:
            rprint(f"\n[red]Error local: {e}[/red]")
            rprint("[yellow]Verifica que Ollama este corriendo (ollama serve)[/yellow]")
            return None, 0
