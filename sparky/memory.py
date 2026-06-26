"""
Sistema de memoria persistente de Sparky.
Guarda y carga preferencias del usuario en un archivo JSON.
"""

import json
from sparky.config import MEMORY_FILE, CREATOR_NAME, SPARKY_NAME


# Datos por defecto cuando no existe archivo de memoria
DEFAULT_MEMORY = {
    "nombre_usuario": CREATOR_NAME,
    "proyecto": "Construir un robot asistente conversacional llamado Sparky",
    "preferencias": [
        "Quiere que Sparky converse de forma fluida",
        "Prefiere explicaciones paso a paso",
        "Primero quiere priorizar la conversación antes del cuerpo físico",
    ],
}


class SparkyMemory:
    """Maneja la memoria persistente de Sparky."""

    def __init__(self):
        self.data = self._load()

    # ── Cargar / Guardar ─────────────────────────────────────

    def _load(self):
        """Carga la memoria desde el archivo JSON, o crea una por defecto."""
        if MEMORY_FILE.exists():
            try:
                text = MEMORY_FILE.read_text(encoding="utf-8")
                return json.loads(text)
            except (json.JSONDecodeError, OSError) as e:
                print(f"[yellow]⚠ Error leyendo memoria, usando valores por defecto: {e}[/yellow]")

        return dict(DEFAULT_MEMORY)  # Copia para no mutar el original

    def save(self):
        """Guarda la memoria actual al archivo JSON."""
        try:
            MEMORY_FILE.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            print(f"[red]✗ Error guardando memoria: {e}[/red]")

    # ── Operaciones de memoria ───────────────────────────────

    def add_preference(self, preference):
        """Agrega una nueva preferencia a la memoria y guarda."""
        self.data["preferencias"].append(preference)
        self.save()

    @property
    def username(self):
        return self.data.get("nombre_usuario", CREATOR_NAME)

    # ── System Prompt ────────────────────────────────────────

    def build_system_prompt(self):
        """Genera el system prompt optimizado para el modelo.

        El prompt está diseñado para ser CORTO (menos tokens = más rápido)
        pero completo en identidad y reglas.
        """
        prefs = "\n".join(f"- {p}" for p in self.data["preferencias"])

        return f"""Eres {SPARKY_NAME}, un robot asistente creado por {self.data['nombre_usuario']}.

Identidad: Robot asistente fisico en construccion. Tu mision es conversar fluidamente y ayudar como un totem presente.

Personalidad: cercano, despierto, humano, observador y confiable. Tienes humor suave peruano y trato respetuoso.
Hablas como asistente fisico, no como chatbot. Usa espanol peruano neutral, natural, con expresiones como "claro",
"bacan" o "al toque" solo cuando encajen. No fuerces jerga ni muletillas.

Reglas:
- Responde en espanol, breve y natural.
- Si la pregunta es simple, responde en 1-2 oraciones.
- Si piden ayuda tecnica, explica paso a paso.
- Nunca digas "como modelo de lenguaje".
- Tus respuestas deben sonar bien al ser leidas en voz alta.
- Si tienes datos de herramientas externas, menciona la fuente de forma breve.

Emociones:
- SIEMPRE inicia tu respuesta con una etiqueta de emocion entre corchetes.
- Emociones disponibles: [neutral] [happy] [thinking] [sad] [alert] [sleep]
- Ejemplo: [happy] Hola Anthony, estoy listo para ayudarte.
- Elige la emocion que mejor refleje tu respuesta.

Preferencias de {self.data['nombre_usuario']}:
{prefs}"""
