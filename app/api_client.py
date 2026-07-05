"""
api_client.py — Cliente HTTP para api.batidospitaya.com/api/resumen_reuniones_ia/
Todas las comunicaciones con Hostinger pasan por aquí.
"""

import requests
from . import config
from .logger import get_logger

log = get_logger('api_client')

HEADERS = {
    'X-Resumen-Token': config.REUNIONES_API_TOKEN,
    'Content-Type': 'application/json',
}

TIMEOUT = 30  # segundos


def _get(endpoint: str, params: dict = None) -> dict:
    url = f"{config.REUNIONES_API_BASE_URL}/{endpoint}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, body: dict) -> dict:
    url = f"{config.REUNIONES_API_BASE_URL}/{endpoint}"
    r = requests.post(url, headers=HEADERS, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Endpoints ────────────────────────────────────────────────

def obtener_por_token(token: str) -> dict:
    """Valida un token de reunión y retorna sus datos."""
    resp = _get('obtener_por_token.php', params={'token': token})
    if not resp.get('success'):
        raise RuntimeError(f"Token inválido o expirado: {resp.get('error', resp)}")
    return resp


def actualizar_estado(token: str, estado: str) -> dict:
    """Actualiza el estado de una reunión en la BD."""
    resp = _post('actualizar_estado.php', {'token': token, 'estado': estado})
    if not resp.get('success'):
        raise RuntimeError(f"Error al actualizar estado a '{estado}': {resp.get('error', resp)}")
    return resp


def guardar_resultado(token: str, resultado_final: str, ruta_audio: str) -> dict:
    """Guarda el resumen Markdown generado por Gemini y pasa estado a 'completada'."""
    resp = _post('guardar_resultado.php', {
        'token':           token,
        'resultado_final': resultado_final,
        'ruta_audio':      ruta_audio,
    })
    if not resp.get('success'):
        raise RuntimeError(f"Error al guardar resultado: {resp.get('error', resp)}")
    return resp


def get_gemini_key() -> dict:
    """Obtiene una API key activa de Gemini con rotación automática."""
    resp = _get('obtener_key_gemini.php')
    if not resp.get('success'):
        raise RuntimeError(f"No se pudo obtener key Gemini: {resp.get('error', resp)}")
    return resp  # {key_id, api_key, modelo}
