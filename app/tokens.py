"""
tokens.py — Validación de tokens de reunión
"""

from fastapi import HTTPException
from . import api_client
from .logger import get_logger

log = get_logger('tokens')


def validar_token(token: str) -> dict:
    """
    Valida un token de reunión consultando la API de Hostinger.
    Retorna los datos de la reunión si es válido.
    Lanza HTTPException si es inválido, expirado o cerrado.
    """
    if not token or len(token) < 20:
        raise HTTPException(status_code=422, detail="Token de reunión inválido (formato incorrecto)")

    try:
        datos = api_client.obtener_por_token(token)
        return datos
    except RuntimeError as e:
        msg = str(e).lower()
        if 'expirado' in msg or 'expired' in msg:
            raise HTTPException(status_code=403, detail="El token de esta reunión ha expirado (6 horas)")
        if 'cerrad' in msg:
            raise HTTPException(status_code=410, detail="Esta reunión ha sido cerrada. El acceso fue revocado.")
        if 'no encontrad' in msg or 'not found' in msg:
            raise HTTPException(status_code=404, detail="Token de reunión no encontrado")
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")
    except Exception as e:
        log.error(f"Error validando token {token[:8]}...: {e}")
        raise HTTPException(status_code=502, detail=f"Error de conectividad con la API: {e}")
