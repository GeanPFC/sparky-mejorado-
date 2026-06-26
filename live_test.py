"""
Fase 1 — Sparky Live (Gemini): voz humana + ve el entorno, en tiempo real.

Sirve SOLO la página /live (no arranca el pipeline viejo Whisper/Nemotron/Azure,
así no pelean por el micrófono). Abre Chrome en /live, das permiso de micro+cámara
y hablas: Sparky te oye, te ve y responde con voz humana (~0.5s, barge-in nativo).

    venv\\Scripts\\python live_test.py

Requiere GEMINI_API_KEY en sparky/secrets.py. Ctrl+C para salir.
"""
import time
from rich import print as rprint
from sparky.avatar import SparkyAvatar
from sparky.config import GEMINI_API_KEY, GEMINI_LIVE_MODEL

if not GEMINI_API_KEY:
    rprint("[bold red]Falta GEMINI_API_KEY en sparky/secrets.py[/bold red]")
    raise SystemExit(1)

rprint(f"[bold magenta]═══ Sparky Live (Fase 1) — {GEMINI_LIVE_MODEL} ═══[/bold magenta]")
avatar = SparkyAvatar()
avatar.start(open_path="live")     # levanta el server y abre /live
rprint("[green]Sparky Live en http://127.0.0.1:8730/live[/green]")
rprint("[dim]Da permiso de micrófono y cámara, y háblale. Ctrl+C para salir.[/dim]")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    avatar.shutdown()
    rprint("\n[dim]Sparky Live cerrado.[/dim]")
