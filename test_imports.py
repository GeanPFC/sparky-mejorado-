"""Test de imports para Sparky."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("--- Sparky Import Test ---\n")

tests = [
    ("config", "from sparky.config import STT_MODEL, STT_LANGUAGE, GEMINI_LIVE, AVATAR_PORT"),
    ("emotions", "from sparky.emotions import parse_emotion"),
    ("memory", "from sparky.memory import SparkyMemory"),
    ("brain", "from sparky.brain import SparkyBrain"),
    ("voice", "from sparky.voice import SparkyVoice"),
    ("listener", "from sparky.listener import SparkyListener"),
    ("sounddevice", "import sounddevice"),
    ("numpy", "import numpy"),
    ("faster_whisper", "import faster_whisper"),
    ("RealtimeSTT", "import RealtimeSTT"),
    ("azure_speech", "import azure.cognitiveservices.speech"),
    ("google_genai", "from google import genai"),
    ("playwright", "import playwright"),
    ("requests", "import requests"),
]

all_ok = True
for name, imp in tests:
    try:
        exec(imp)
        print(f"  [OK] {name}")
    except Exception as e:
        print(f"  [FALLO] {name}: {e}")
        all_ok = False

print(f"\n{'Todos los imports OK' if all_ok else 'HAY FALLOS'}")
print("--- Listo ---")
