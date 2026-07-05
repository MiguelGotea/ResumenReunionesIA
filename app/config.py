"""
config.py — Configuración centralizada desde variables de entorno
Todos los módulos importan desde aquí, nunca leen .env directamente.
"""

import os
from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Variable de entorno requerida no definida: {key}")
    return val


# ── API Hostinger ──────────────────────────────────────────────
# Token que identifica a este VPS ante api.batidospitaya.com
REUNIONES_API_TOKEN    = _require('REUNIONES_API_TOKEN')
REUNIONES_API_BASE_URL = os.getenv(
    'REUNIONES_API_BASE_URL',
    'https://api.batidospitaya.com/api/resumen_reuniones_ia'
).rstrip('/')

# ── Token ERP ─────────────────────────────────────────────────
# El VPS lo recibe en DELETE /api/audio/{id} para verificar que
# la llamada viene realmente del ERP (aprobar.php)
RESUMEN_TOKEN_ERP = _require('RESUMEN_TOKEN_ERP')

# ── Almacenamiento de audio ──────────────────────────────────
AUDIO_DIR = os.getenv('AUDIO_DIR', '/opt/resumen-reuniones-ia/audio')
os.makedirs(AUDIO_DIR, exist_ok=True)

# ── Servidor ──────────────────────────────────────────────────
PORT = int(os.getenv('PORT', '8888'))
