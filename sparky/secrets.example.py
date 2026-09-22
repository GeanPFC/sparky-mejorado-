"""Plantilla de secretos. Copia este archivo como secrets.py y pon tu clave.

    cp sparky/secrets.example.py sparky/secrets.py

secrets.py está en .gitignore y NO se sube al repositorio.
Alternativa: define la variable de entorno NVIDIA_API_KEY.
"""

# Clave de la API de NVIDIA (https://build.nvidia.com)
NVIDIA_API_KEY = "nvapi-PON_TU_CLAVE_AQUI"

# Clave de Azure Speech (portal.azure.com → recurso Speech → Claves y punto de conexión)
AZURE_SPEECH_KEY = "PON_TU_CLAVE_DE_AZURE_AQUI"

# Clave de Simli (app.simli.com → API key). Opcional: activa el avatar fotorrealista.
SIMLI_API_KEY = ""
