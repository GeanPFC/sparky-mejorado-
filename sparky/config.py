"""
Configuración centralizada de Sparky.
Cambia estos valores para ajustar el comportamiento sin tocar otros módulos.
"""

import os
from pathlib import Path

# ── Modelo LOCAL (Ollama) ─────────────────────────────────────
LOCAL_ENABLED = False              # full-API: NO usar Ollama local (la PC no corre modelos locales)
LOCAL_MODEL = "llama3.2:1b"
LOCAL_OPTIONS = {
    "num_predict": 150,
    "num_ctx": 2048,
    "temperature": 0.7,
}

# ── Modelo en NUBE (NVIDIA API) ───────────────────────────────
CLOUD_ENABLED = True
CLOUD_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# La clave se lee de sparky/secrets.py (no versionado) o de la variable de entorno
try:
    from sparky.secrets import NVIDIA_API_KEY as CLOUD_API_KEY
except ImportError:
    CLOUD_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
CLOUD_MODEL = "nvidia/nemotron-3-nano-30b-a3b"   # probado: rápido y respeta el formato [emoción]
# Nemotron razona por defecto y vuelca el "pensamiento" al texto → a la voz. Esto lo APAGA.
# (probado: thinking=False da respuesta limpia en ~0.8 s; sin esto, suelta razonamiento en inglés)
# Si vuelves a un modelo no-razonador (p.ej. meta/llama-3.1-8b-instruct), deja CLOUD_EXTRA = {}.
CLOUD_EXTRA = {"chat_template_kwargs": {"thinking": False}}
CLOUD_MAX_TOKENS = 200
CLOUD_TEMPERATURE = 0.7
CLOUD_TIMEOUT = 20                  # el endpoint gratis a veces tarda en el 1er token; 5s era muy corto

# ── Conversación ──────────────────────────────────────────────
MAX_HISTORY_MESSAGES = 4

# ── Memoria ───────────────────────────────────────────────────
MEMORY_FILE = Path(__file__).parent.parent / "sparky_memory.json"

# ── Voz (Piper TTS) ──────────────────────────────────────────
VOICE_ENABLED = True
PIPER_EXE = Path(__file__).parent.parent / "piper" / "piper" / "piper.exe"
PIPER_MODEL = Path(__file__).parent.parent / "piper" / "piper" / "es_MX-ald-medium.onnx"
PIPER_SAMPLE_RATE = 22050
PIPER_SENTENCE_SILENCE = 0.15       # Silencio tras cada frase (default piper: 0.2)
PIPER_TMP = Path(__file__).parent.parent / "tmp_tts"  # WAVs temporales del proceso caliente
VOICE_RATE = 165                    # Velocidad pyttsx3 (fallback)

# ── Filler (muletilla para tapar la latencia del LLM) ────────
FILLER_ENABLED = True
FILLER_PHRASES = ["Mmm,", "A ver,", "Déjame ver,"]

# ── Motor de voz (TTS) ───────────────────────────────────────
# "azure" (voz humana + labios exactos) | "piper" (local, gratis)
TTS_ENGINE = "azure"

# ── Azure Speech (voz neuronal + visemas) ────────────────────
AZURE_SPEECH_REGION = "eastus"     # la región de tu recurso (eastus, brazilsouth, etc.)
AZURE_VOICE = "es-PE-CamilaNeural"  # voz peruana; si quieres hombre prueba "es-PE-AlexNeural"
# Alternativa premium mexicana con visemas probada: "es-MX-Ximena:DragonHDLatestNeural"
try:
    from sparky.secrets import AZURE_SPEECH_KEY
except ImportError:
    AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")

# ── STT (RealtimeSTT + faster-whisper) ───────────────────────
STT_MODEL = "tiny"                  # tiny=rápido (~1s), base=equilibrado (~2s), small=preciso. Si oye mal, sube a "base"
STT_LANGUAGE = "es"

# ── Avatar visual (cara en pantalla vertical) ────────────────
AVATAR_ENABLED = True
AVATAR_PORT = 8730
AVATAR_KIOSK = True                 # abrir Chrome en modo kiosko (pantalla completa)
AVATAR_3D = True                    # True: avatar humano 3D (/3d, audio en navegador); False: cara 2D
TOTEM_MODE = True                   # True: escucha sola por micrófono (sin pulsar Enter)
BARGE_IN = True                     # interrupción real (como un humano). REQUIERE auriculares o
                                    # micrófono con cancelación de eco; con altavoz abierto Sparky se
                                    # oye a sí mismo y se corta solo (pon False si no tienes AEC).

# ── Cámara (presencia + reconocimiento facial, todo en el navegador) ──
CAMERA_ENABLED = True
FACE_MATCH_THRESHOLD = 0.5          # distancia euclídea máx para "misma persona" (face-api). Sube=más laxo
PRESENCE_GREET_COOLDOWN = 30        # s; no volver a saludar a la misma persona antes de esto
FACES_FILE = Path(__file__).parent.parent / "faces.json"   # rostros registrados (descriptores)

# ── Imitar tus gestos (MediaPipe Pose en el navegador) ───────
POSE_MIRROR_ENABLED = True          # levantas la mano → el avatar la levanta (usa la misma webcam)

# ── Gemini Live (voz humana + visión en vivo, todo en un stream) ──
try:
    from sparky.secrets import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_LIVE_MODEL = "gemini-3.1-flash-live-preview"   # probado: audio nativo + visión
# True: el avatar 3D (/3d) usa Gemini Live como cerebro+voz+visión (en vez de Whisper/Nemotron/Azure).
# main.py NO arranca el pipeline viejo; el navegador hace todo. Pon False para volver al viejo.
GEMINI_LIVE = True

# ── Computer Use (Gemini 3.5 Flash controla un navegador supervisado) ──
# Apagado por defecto: se integra como habilidad opcional, no reemplaza Gemini Live.
COMPUTER_USE_ENABLED = False
COMPUTER_USE_MODEL = "gemini-3.5-flash"
COMPUTER_USE_ENVIRONMENT = "browser"      # MVP seguro: solo navegador, no escritorio completo
COMPUTER_USE_HEADLESS = False             # visible para supervisar lo que hace Sparky
COMPUTER_USE_TURN_LIMIT = 8               # límite duro de pasos por tarea
COMPUTER_USE_API_TIMEOUT = 180            # Computer Use puede tardar en el primer paso
COMPUTER_USE_REQUIRE_CONFIRMATION = True  # pedir confirmación para acciones sensibles
COMPUTER_USE_SCREEN_WIDTH = 1280
COMPUTER_USE_SCREEN_HEIGHT = 800
COMPUTER_USE_START_URL = "https://www.bing.com"
COMPUTER_USE_ALLOWED_DOMAINS = []         # vacío = permite navegar, pero con políticas sensibles activas
COMPUTER_USE_CONFIRMATION_TIMEOUT = 120   # segundos esperando aprobación del usuario

# ── Herramientas gratuitas (APIs no-IA, sin API key) ─────────
FREE_TOOLS_ENABLED = True
FREE_TOOLS_CACHE_SECONDS = 600
FREE_TOOLS_TIMEOUT = 12

# ── Identidad de Sparky ───────────────────────────────────────
SPARKY_NAME = "Sparky"
CREATOR_NAME = "Anthony"
