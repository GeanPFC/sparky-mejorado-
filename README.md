# Sparky — Tótem conversacional con avatar IA

Asistente virtual presencial para una pantalla vertical: la persona se acerca,
habla por voz y un **avatar humano 3D** le responde con voz natural en español,
moviendo los labios sincronizados con el audio.

## Arquitectura

```
Micrófono → STT (RealtimeSTT/Whisper) → Cerebro (LLM nube/local) → Voz (Piper TTS)
                                                                        │
                                          Avatar 3D en navegador ←──────┘
                                  (TalkingHead.js + HeadAudio = lip-sync)
```

- **Cascada optimizada** (no speech-to-speech) para latencia ~1 s, gratis y offline.
- El audio de Piper se reproduce en el **navegador**, donde HeadAudio detecta los
  visemas del audio y mueve los labios (independiente del idioma).

### Módulos (`sparky/`)
| Archivo | Rol |
|---|---|
| `listener.py` | Escucha por micrófono (RealtimeSTT + faster-whisper) con barge-in |
| `brain.py` | LLM híbrido: nube NVIDIA → fallback Ollama local, streaming por oraciones |
| `voice.py` | Piper TTS (proceso caliente); enruta el audio al navegador o al altavoz |
| `avatar.py` | Servidor http.server: sirve el avatar y la cola de audio |
| `avatar3d.html` | Avatar humano 3D (TalkingHead.js + HeadAudio) con lip-sync |
| `avatar.html` | Cara 2D estilizada (alternativa, `AVATAR_3D=False`) |
| `emotions.py` / `memory.py` | Emociones y memoria persistente |

## Modo Lima Expresa (asesor comercial)

Con `GEMINI_LIVE = True` el tótem actúa como vendedor de Lima Expresa: pregunta de qué
distrito viene el visitante, muestra datos reales de la encuesta Origen-Destino (2018) en
una tarjeta minimalista, camina a un lado para presentarla y cierra invitando a afiliarse.

- **Datos:** la encuesta no se versiona (es del cliente). Genérala una vez en el tótem:
  `python -m sparky.od_data build "ruta\Origen -Destino-V01.xlsx"` → `sparky/data/od.json`.
  Autoprueba: `python -m sparky.od_data`.
- **Herramientas Gemini:** `consultar_encuesta_od` (distrito / tema / plaza), `ocultar_datos`, `caminar`.
- **Claves** en `sparky/secrets.py`: `GEMINI_API_KEY` (obligatoria), `SIMLI_API_KEY`
  (opcional: cara fotorrealista, solo busto; se activa con `SIMLI_ENABLED = True`).
- Lo que dice el avatar queda en `avatar.log` (líneas `dijo:`) para revisar el discurso.

## Requisitos

- Python 3.13, Windows
- [Ollama](https://ollama.com) con `llama3.2:1b` (fallback local)
- **Piper TTS** + voz `es_MX-ald-medium` en `piper/piper/` (binarios incluidos)
- Una clave de la [API de NVIDIA](https://build.nvidia.com) (modelo en la nube)

## Configuración

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# Clave de NVIDIA (no se versiona):
copy sparky\secrets.example.py sparky\secrets.py
# …y edita sparky/secrets.py con tu clave.
```

Ajustes en `sparky/config.py`: `AVATAR_3D`, `TOTEM_MODE`, modelos, latencia, etc.

## Uso

```bash
venv\Scripts\python main.py
```
Abre **http://127.0.0.1:8730/3d** (o se abre solo en kiosko). En modo tótem
escucha sola tras saludar: habla y el avatar te responde.

Pruebas:
- `test_avatar3d.py` — el avatar habla en bucle (demo de voz + lip-sync)
- `test_voice.py` — latencia del TTS · `test_avatar.py` — cara 2D

## Notas de uso

- **Eco mic↔altavoz**: resuelto por turnos (se vacía el buffer antes de escuchar).
  Para permitir interrumpir a Sparky mientras habla, usa auriculares o un micrófono
  con cancelación de eco y pon `BARGE_IN = True` en `config.py`.
- El avatar carga sus librerías por **CDN** (necesita internet). Empaquetado
  offline: pendiente, añadir si el tótem irá sin red.

## Pendientes (opcionales)

- Empaquetado offline de los JS/modelo del avatar
- Detección de presencia · elección del rostro final del avatar
