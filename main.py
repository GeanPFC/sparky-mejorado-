"""
Sparky — Robot Asistente Conversacional v3.1
Punto de entrada — Conversación natural con barge-in.

Flujo:
    1. Sparky saluda
    2. Presiona Enter → modo micrófono continuo
    3. Habla → Sparky responde (oración por oración)
    4. Si hablas MIENTRAS Sparky responde → lo interrumpe y escucha
    5. Automáticamente vuelve a escuchar → Repite sin tocar teclado

Barge-in:
    RealtimeSTT detecta tu voz incluso durante la reproducción TTS.
    on_recording_start → voice.stop_speaking() → Sparky se calla → escucha
"""

import re
import time
import threading
from rich import print as rprint
from sparky.config import (
    LOCAL_MODEL, CLOUD_ENABLED, CLOUD_MODEL, SPARKY_NAME, TOTEM_MODE,
    CAMERA_ENABLED, PRESENCE_GREET_COOLDOWN,
)
from sparky.memory import SparkyMemory
from sparky.brain import SparkyBrain
from sparky.voice import SparkyVoice
from sparky.listener import SparkyListener
from sparky.avatar import SparkyAvatar
from sparky.gestures import detect_gesture
from sparky.config import GEMINI_LIVE


def main_gemini_live():
    """Modo Gemini Live: el navegador (avatar3d.html) hace TODO — oye, ve, piensa y
    habla vía Gemini Live. Python solo sirve el avatar y se queda vivo."""
    rprint(f"\n[bold magenta]═══ {SPARKY_NAME} — Gemini Live ═══[/bold magenta]")
    rprint("[dim]El navegador maneja voz + visión vía Gemini. Ctrl+C para salir.[/dim]")
    avatar = SparkyAvatar()
    avatar.start()                 # sirve y abre /3d (que ahora arranca Gemini Live)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        avatar.shutdown()
        rprint("\n[dim]Sparky cerrado.[/dim]")


def main():
    # ── Inicializar módulos ──────────────────────────────────
    rprint(f"\n[bold magenta]═══ {SPARKY_NAME} v3.1 ═══[/bold magenta]")

    if CLOUD_ENABLED:
        rprint(f"[dim]Nube: {CLOUD_MODEL} | Local: {LOCAL_MODEL}[/dim]")
    else:
        rprint(f"[dim]Modelo: {LOCAL_MODEL} (solo local)[/dim]")

    memory = SparkyMemory()
    brain = SparkyBrain()
    voice = SparkyVoice()
    listener = SparkyListener()
    avatar = SparkyAvatar(voice, brain)
    voice.set_avatar(avatar)  # modo 3D: el audio se reproduce en el navegador

    # Conectar voice ↔ listener para barge-in
    listener.set_voice(voice)

    # Lanzar la cara en pantalla (lee voice.is_speaking y brain.last_emotion)
    avatar.start()

    # ── Saludo por presencia (cámara) ────────────────────────
    # El navegador detecta cuando alguien aparece y avisa; un hilo lo saluda.
    # ponytail: hilo simple con cooldown; saluda solo si Sparky no está hablando.
    # Si NO hay AEC/auriculares, el saludo entra por el micro como eco (ver BARGE_IN).
    def presence_watcher():
        last_greet = {}  # nombre → instante del último saludo
        while True:
            time.sleep(0.5)
            name = avatar.pop_presence()
            if name is None or voice.is_speaking:
                continue
            now = time.time()
            if now - last_greet.get(name, 0) < PRESENCE_GREET_COOLDOWN:
                continue
            last_greet[name] = now
            if name:
                voice.speak_now(f"Hola {name}, qué bueno verte de nuevo.")
            else:
                voice.speak_now("Hola, soy Sparky. Si quieres que te reconozca, dime: recuérdame como tu nombre.")

    if CAMERA_ENABLED:
        threading.Thread(target=presence_watcher, daemon=True).start()

    rprint("[dim]Escribe texto o presiona Enter para modo microfono continuo[/dim]")
    rprint("[dim]En modo mic: habla naturalmente, interrumpe a Sparky cuando quieras[/dim]\n")

    # Saludo inicial (se reproduce en el navegador; bloquea hasta terminar)
    voice.speak_now(f"Hola {memory.username}, soy {SPARKY_NAME}. Estoy listo.")

    # ── Estado ───────────────────────────────────────────────
    # Modo tótem: escucha directamente por micrófono, sin pulsar Enter.
    # El recorder se crea en el primer listen_vad (tras el saludo, evita eco).
    mic_mode = TOTEM_MODE
    if mic_mode:
        rprint("[bold magenta]🎤 Escuchando — habla cuando quieras.[/bold magenta]")

    # ── Loop principal ───────────────────────────────────────
    while True:
        try:
            user_text = None

            if mic_mode:
                # ── Modo micrófono continuo (con barge-in) ───
                user_text = listener.listen_vad()
                if not user_text:
                    continue
            else:
                # ── Modo teclado ─────────────────────────────
                keyboard_text = listener.listen_keyboard()
                if keyboard_text is None:
                    mic_mode = True
                    rprint("[bold magenta]🎤 Modo microfono activado![/bold magenta]")
                    rprint("[dim]Habla naturalmente. Puedes interrumpir a Sparky.[/dim]")
                    rprint("[dim]Escribe 'teclado' para volver al modo texto.[/dim]")
                    user_text = listener.listen_vad()
                    if not user_text:
                        continue
                else:
                    user_text = keyboard_text

            if not user_text:
                continue

            # ── Comando: salir ───────────────────────────────
            if user_text.lower() in ("salir", "exit", "terminar"):
                voice.speak_now("Listo, entro en modo descanso. Seguimos luego!")
                memory.save()
                break

            # ── Comando: volver a teclado ────────────────────
            if user_text.lower() in ("teclado", "texto"):
                mic_mode = False
                rprint("[bold magenta]⌨️ Modo teclado activado.[/bold magenta]")
                continue

            # ── Comando: registrar rostro ("recuérdame como X") ──
            # Exige "como" y toma la palabra siguiente (evita agarrar muletillas como "ya").
            m = re.search(r"recu[eé]rdame\s+como\s+([a-záéíóúñ]+)", user_text.lower())
            if m:
                name = m.group(1).capitalize()
                avatar.request_enroll(name)
                voice.speak_now(f"Mírame a la cámara un momento, {name}.")
                continue

            # ── Gesto por voz: "levanta la mano", "baila", etc. ──
            gesture = detect_gesture(user_text)
            if gesture:
                avatar.play_gesture(gesture)

            # ── PROCESSING: Pensar y responder ───────────────
            rprint("[yellow]Sparky esta pensando...[/yellow]")

            response, seconds = brain.think(
                user_text=user_text,
                system_prompt=memory.build_system_prompt(),
                voice=voice,
            )

            if response is None:
                voice.speak_now("Tuve un problema pensando.")
                continue

            engine_tag = brain.last_engine or "?"
            rprint(f"[dim]{seconds}s via {engine_tag}[/dim]")

            # En modo mic, automáticamente vuelve a escuchar (loop)

        except KeyboardInterrupt:
            rprint("\n[dim]Ctrl+C detectado[/dim]")
            voice.speak_now("Nos vemos!")
            memory.save()
            break

    # Cleanup
    listener.shutdown()
    voice.shutdown()
    avatar.shutdown()


if __name__ == "__main__":
    main_gemini_live() if GEMINI_LIVE else main()
