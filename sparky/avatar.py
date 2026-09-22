"""
Avatar visual de Sparky — cara animada en el navegador (pantalla vertical).

Sirve una página full-screen con una cara que:
- mueve la boca cuando Sparky habla  (voice.is_speaking)
- cambia de expresión según la emoción (brain.last_emotion)

Arquitectura perezosa: http.server de la stdlib → CERO dependencias nuevas.
El navegador consulta /state cada ~120 ms; la animación de la boca es CSS en
el cliente, así que no necesita sincronía fina con el audio (como hacen los
avatares reales).
"""

import json
import time
import shutil
import threading
import webbrowser
import subprocess
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from rich import print as rprint
from sparky.config import (
    AVATAR_ENABLED, AVATAR_PORT, AVATAR_KIOSK, AVATAR_3D, SPARKY_NAME, TTS_ENGINE,
    CAMERA_ENABLED, FACES_FILE, POSE_MIRROR_ENABLED,
    GEMINI_API_KEY, GEMINI_LIVE_MODEL, GEMINI_LIVE, SIMLI_API_KEY, SIMLI_FACE_ID, SIMLI_ENABLED,
)
import requests
from sparky.computer_agent import ComputerAgent
from sparky.free_tools import call_free_tool

_LOG_FILE = Path(__file__).parent.parent / "avatar.log"

_HTML = (Path(__file__).parent / "avatar.html").read_text(encoding="utf-8")
_HTML_3D = (Path(__file__).parent / "avatar3d.html").read_text(encoding="utf-8")
_HTML_LIVE = (Path(__file__).parent / "live.html").read_text(encoding="utf-8")

# Rutas típicas de Chrome en Windows para el modo kiosko
_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


class SparkyAvatar:
    """Cara animada de Sparky servida por un http.server local."""

    def __init__(self, voice=None, brain=None):
        self.voice = voice
        self.brain = brain
        self._server = None
        self._instance_id = str(int(time.time() * 1000))

        # Cola de audio para reproducir en el navegador (modo 3D)
        self._audio = []                 # [{id, data(bytes)}] aún no servidos al navegador
        self._gen = 0                    # generación: sube en barge-in para cancelar
        self._next_id = 0
        self._audio_lock = threading.Lock()
        self._busy = False               # ¿el navegador está reproduciendo ahora mismo?

        # Gesto pendiente (se reproduce una vez; el id sube en cada orden)
        self._gesture = None
        self._gesture_id = 0

        # Cámara: rostros conocidos [{name, descriptor[128]}], presencia y enrolamiento
        self._faces = self._load_faces()
        self._presence = None            # nombre (o "") de quien acaba de aparecer; lo lee main
        self._enroll = None              # nombre a registrar cuando el navegador capture la cara
        self._enroll_id = 0
        self.computer_agent = ComputerAgent()

    def browser_busy(self):
        return self._busy

    def play_gesture(self, name):
        """Encola un gesto para que el navegador lo reproduzca una vez."""
        with self._audio_lock:
            self._gesture = name
            self._gesture_id += 1

    # ── Cámara: presencia + reconocimiento facial ────────────

    def _load_faces(self):
        if FACES_FILE.exists():
            try:
                return json.loads(FACES_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def set_presence(self, name):
        """El navegador avisa que alguien apareció (name="" si es desconocido)."""
        with self._audio_lock:
            self._presence = name

    def pop_presence(self):
        """Devuelve (y limpia) el nombre de quien apareció, o None si no hay nada nuevo."""
        with self._audio_lock:
            p, self._presence = self._presence, None
            return p

    def request_enroll(self, name):
        """Pide al navegador capturar el rostro actual y guardarlo como `name`."""
        with self._audio_lock:
            self._enroll = name
            self._enroll_id += 1

    def add_face(self, name, descriptor):
        """Guarda (o reemplaza) un rostro. Lo llama el POST /enroll del navegador."""
        with self._audio_lock:
            self._faces = [f for f in self._faces if f["name"] != name]
            self._faces.append({"name": name, "descriptor": descriptor})
            faces = list(self._faces)
        try:
            FACES_FILE.write_text(json.dumps(faces, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ── Cola de audio (navegador reproduce → HeadAudio lip-sync) ──

    def push_audio(self, wav_bytes, visemes=None):
        """Encola un WAV (+ visemas opcionales) para el navegador. Devuelve su id."""
        with self._audio_lock:
            cid = self._next_id
            self._next_id += 1
            self._audio.append({"id": cid, "data": wav_bytes, "visemes": visemes})
            return cid

    def cancel_audio(self):
        """Barge-in: cancela todo lo pendiente y avisa al navegador (sube gen)."""
        with self._audio_lock:
            self._gen += 1
            self._audio.clear()

    def take_audio(self, cid):
        """El navegador descarga un clip (dict con data+visemas): se entrega una vez."""
        with self._audio_lock:
            for i, c in enumerate(self._audio):
                if c["id"] == cid:
                    return self._audio.pop(i)
        return None

    def _state(self):
        emotion = (self.brain.last_emotion if self.brain else "neutral") or "neutral"
        speaking = bool(self.voice and self.voice.is_speaking)
        with self._audio_lock:
            pending = [{"id": c["id"], "url": f"/audio/{c['id']}"} for c in self._audio]
            gen = self._gen
            gesture, gesture_id = self._gesture, self._gesture_id
            enroll, enroll_id = self._enroll, self._enroll_id
        return {"speaking": speaking, "emotion": emotion, "name": SPARKY_NAME,
                "instance_id": self._instance_id,
                "gen": gen, "audio": pending, "tts": TTS_ENGINE,
                "gesture": gesture, "gesture_id": gesture_id,
                "enroll": enroll, "enroll_id": enroll_id, "camera": CAMERA_ENABLED,
                "pose_mirror": POSE_MIRROR_ENABLED,
                "computer": self.computer_agent.status()}

    def start(self, open_path=None):
        if not AVATAR_ENABLED:
            return
        avatar = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"   # keep-alive: evita ERR_CONNECTION_RESET bajo sondeo

            def log_message(self, *a):
                pass  # silenciar logs HTTP

            def do_GET(self):
              try:
                if self.path.startswith("/busy"):
                    v = parse_qs(urlparse(self.path).query).get("v", ["0"])[0]
                    avatar._busy = (v == "1")
                    body = b"ok"
                    ctype = "text/plain"
                elif self.path.startswith("/log"):
                    msg = parse_qs(urlparse(self.path).query).get("m", [""])[0]
                    line = "[" + time.strftime("%H:%M:%S") + "] navegador: " + msg
                    try:
                        with open(_LOG_FILE, "a", encoding="utf-8") as f:
                            f.write(line + "\n")
                    except OSError:
                        pass
                    # print plano y ascii-safe (rich/cp1252 en Windows revientan con emojis)
                    try:
                        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
                    except Exception:
                        pass
                    body = b"ok"
                    ctype = "text/plain"
                elif self.path.startswith("/audio/"):
                    try:
                        cid = int(self.path.rsplit("/", 1)[1].split("?")[0])
                    except ValueError:
                        cid = -1
                    clip = avatar.take_audio(cid)
                    if clip is None:
                        self.send_error(404, "audio no disponible")
                        return
                    import base64
                    payload = {"audio": base64.b64encode(clip["data"]).decode("ascii")}
                    if clip.get("visemes"):
                        payload.update(clip["visemes"])  # visemes, vtimes, vdurations
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                elif self.path.startswith("/faces"):
                    body = json.dumps(avatar._faces).encode("utf-8")
                    ctype = "application/json"
                elif self.path.startswith("/presence"):
                    name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
                    avatar.set_presence(name)
                    body = b"ok"
                    ctype = "text/plain"
                elif self.path.startswith("/agent/status"):
                    body = json.dumps(avatar.computer_agent.status()).encode("utf-8")
                    ctype = "application/json"
                elif self.path.startswith("/state"):
                    body = json.dumps(avatar._state()).encode("utf-8")
                    ctype = "application/json"
                elif self.path.startswith("/live_config"):
                    body = json.dumps({"key": GEMINI_API_KEY, "model": GEMINI_LIVE_MODEL,
                                       "name": SPARKY_NAME, "gemini_live": GEMINI_LIVE,
                                       "instance_id": avatar._instance_id,
                                       "simli": SIMLI_ENABLED and bool(SIMLI_API_KEY)}).encode("utf-8")
                    ctype = "application/json"
                elif self.path.startswith("/simli_token"):
                    # El token se crea aquí: la clave de Simli nunca llega al navegador.
                    r = requests.post("https://api.simli.ai/compose/token", timeout=15,
                                      headers={"x-simli-api-key": SIMLI_API_KEY},
                                      json={"faceId": SIMLI_FACE_ID, "handleSilence": True,
                                            "maxSessionLength": 3600, "maxIdleTime": 120})
                    body = r.content
                    ctype = "application/json"
                elif self.path.startswith("/live"):
                    body = _HTML_LIVE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                elif self.path.startswith("/3d"):
                    body = _HTML_3D.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    body = _HTML.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
              except Exception as e:
                try:
                    self.send_error(500, str(e))
                except Exception:
                    pass

            def do_POST(self):
              try:
                if self.path.startswith("/enroll"):
                    length = int(self.headers.get("Content-Length", 0))
                    obj = json.loads(self.rfile.read(length).decode("utf-8"))
                    avatar.add_face(obj["name"], obj["descriptor"])
                    body = b"ok"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/agent/start"):
                    length = int(self.headers.get("Content-Length", 0))
                    obj = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    body = json.dumps(avatar.computer_agent.start_task(obj.get("task", ""))).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/agent/confirm"):
                    length = int(self.headers.get("Content-Length", 0))
                    obj = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    body = json.dumps(avatar.computer_agent.confirm(bool(obj.get("approved")))).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/agent/stop"):
                    body = json.dumps(avatar.computer_agent.stop()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/tools/call"):
                    length = int(self.headers.get("Content-Length", 0))
                    obj = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    result = call_free_tool(obj.get("name", ""), obj.get("args", {}))
                    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)
              except Exception as e:
                try:
                    self.send_error(500, str(e))
                except Exception:
                    pass

        # En Windows, SO_REUSEADDR deja que DOS servidores tomen el mismo puerto
        # y el navegador se engancha al fantasma. Lo desactivamos para fallar claro.
        ThreadingHTTPServer.allow_reuse_address = False
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", AVATAR_PORT), Handler)
        except OSError:
            rprint(f"[bold red]Puerto {AVATAR_PORT} ya ocupado por otro Sparky.[/bold red]")
            rprint("[yellow]Cierra los 'python' viejos (Administrador de tareas) y reinicia.[/yellow]")
            return
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

        path = open_path if open_path is not None else ("3d" if AVATAR_3D else "")
        url = f"http://127.0.0.1:{AVATAR_PORT}/" + path
        rprint(f"[dim]Avatar: {url}[/dim]")
        self._open_browser(url)

    def _open_browser(self, url):
        """Abre Chrome en modo kiosko si se puede; si no, el navegador por defecto."""
        if AVATAR_KIOSK:
            chrome = shutil.which("chrome") or next(
                (p for p in _CHROME_PATHS if Path(p).exists()), None
            )
            if chrome:
                try:
                    subprocess.Popen([chrome, "--kiosk", "--noerrdialogs",
                                      "--disable-infobars",
                                      "--autoplay-policy=no-user-gesture-required",
                                      "--use-fake-ui-for-media-stream",  # auto-acepta la cámara (kiosko)
                                      url])
                    return
                except Exception:
                    pass
        webbrowser.open(url)  # fallback: pestaña normal (pulsa F11 para pantalla completa)

    def shutdown(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
