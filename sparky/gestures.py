"""
Detección de gestos por voz: mapea lo que dice el usuario a un gesto del avatar.

TalkingHead.js trae estos gestos de mano nativos:
    handup, index, ok, thumbup, thumbdown, side, shrug, namaste
"dance" NO es nativo (requiere un clip de animación Mixamo); avatar3d.html lo
resuelve con una secuencia de gestos como placeholder.
"""

# nombre de gesto (TalkingHead) → frases que lo disparan
_GESTURES = {
    "handup":    ["levanta la mano", "alza la mano", "sube la mano", "levanta tu mano", "mano arriba"],
    "namaste":   ["saluda", "haz un saludo", "dame un saludo"],
    "thumbup":   ["pulgar arriba", "bien hecho", "buen trabajo", "me gusta", "perfecto"],
    "thumbdown": ["pulgar abajo", "no me gusta", "qué mal"],
    "ok":        ["haz ok", "señal de ok", "de acuerdo"],
    "shrug":     ["no sé", "no sabes", "encógete", "qué más da"],
    "index":     ["señala", "apunta"],
    "dance":     ["baila", "ponte a bailar", "una danza", "muévete al ritmo"],
}


def detect_gesture(text):
    """Devuelve el nombre del gesto a reproducir, o None si no hay orden de gesto."""
    t = text.lower()
    for gesture, phrases in _GESTURES.items():
        if any(p in t for p in phrases):
            return gesture
    return None


if __name__ == "__main__":
    assert detect_gesture("oye, levanta la mano por favor") == "handup"
    assert detect_gesture("ahora baila un poco") == "dance"
    assert detect_gesture("bien hecho amigo") == "thumbup"
    assert detect_gesture("cuéntame del clima") is None
    print("gestures.py OK")
