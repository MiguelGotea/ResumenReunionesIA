"""
gemini_client.py — Upload de audio a Gemini Files API y generación de resumen

Patrón: igual que HikvisionAnalisisIA/src/analyzer.py pero para audio.
- Upload resumable a Files API
- Polling hasta estado ACTIVE
- generateContent con audio nativo
- Borrado del archivo en Gemini (finally)
"""

import requests
import time
from pathlib import Path

from .logger import get_logger

log = get_logger('gemini_client')

GEMINI_UPLOAD_URL  = "https://generativelanguage.googleapis.com/upload/v1beta/files"
GEMINI_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_FILES_BASE  = "https://generativelanguage.googleapis.com/v1beta/{name}"

# ── Mime types de audio soportados por Gemini Files API ──────
_SUPPORTED_AUDIO_MIMES = {
    'webm': 'audio/webm',
    'ogg':  'audio/ogg',
    'mp3':  'audio/mpeg',
    'mp4':  'audio/mp4',
    'wav':  'audio/wav',
    'flac': 'audio/flac',
}

# ── System prompt ─────────────────────────────────────────────
_SYSTEM_PROMPT_TEMPLATE = """\
Eres un asistente corporativo experto en análisis y documentación de reuniones de negocio.
Has recibido el audio completo de una reunión corporativa de Batidos Pitaya, una cadena de \
batidos y bebidas naturales en Nicaragua.

Contexto de la reunión:
- Título: {titulo}
- Descripción: {descripcion}

Tu tarea es generar un resumen ejecutivo profesional y completo en español.

IMPORTANTE:
- Organiza el resumen usando EXACTAMENTE estos encabezados Markdown:
  ## Decisiones Tomadas
  ## Tareas Asignadas
  ## Acuerdos y Compromisos
  ## Puntos de Seguimiento
- En "Tareas Asignadas", indica el responsable si se menciona en el audio.
- En "Puntos de Seguimiento", incluye: fechas límite, riesgos mencionados, y pendientes para la próxima reunión.
- Si alguna sección no tiene contenido relevante, indícalo brevemente (ej: "No se identificaron tareas específicas.")
- Usa texto limpio, profesional y en párrafos o listas según corresponda.
- NO inventes información que no esté en el audio.
- NO uses tablas complejas ni JSON.
- La respuesta debe ser SOLO el resumen en Markdown, sin texto introductorio ni conclusivo.\
"""


def _detect_mime(audio_path: Path) -> str:
    """Detecta el mime type según la extensión del archivo."""
    ext = audio_path.suffix.lstrip('.').lower()
    return _SUPPORTED_AUDIO_MIMES.get(ext, 'audio/webm')


def _upload_audio(audio_path: Path, api_key: str) -> tuple[str, str]:
    """Sube el audio a Gemini Files API. Retorna (file_uri, file_name)."""
    file_size    = audio_path.stat().st_size
    display_name = audio_path.name
    mime_type    = _detect_mime(audio_path)

    log.info(f"📤 Subiendo audio a Gemini ({file_size / (1024*1024):.1f} MB, {mime_type})...")

    # Iniciar upload resumable
    init_resp = requests.post(
        f"{GEMINI_UPLOAD_URL}?key={api_key}",
        headers={
            'X-Goog-Upload-Protocol':            'resumable',
            'X-Goog-Upload-Command':             'start',
            'X-Goog-Upload-Header-Content-Length': str(file_size),
            'X-Goog-Upload-Header-Content-Type': mime_type,
            'Content-Type':                      'application/json',
        },
        json={'file': {'display_name': display_name}},
        timeout=30,
    )
    init_resp.raise_for_status()

    upload_url = init_resp.headers.get('X-Goog-Upload-URL')
    if not upload_url:
        raise RuntimeError("Gemini no devolvió upload URL en el header")

    # Subir bytes del archivo
    with open(audio_path, 'rb') as f:
        audio_bytes = f.read()

    upload_resp = requests.post(
        upload_url,
        headers={
            'Content-Length':         str(file_size),
            'X-Goog-Upload-Offset':   '0',
            'X-Goog-Upload-Command':  'upload, finalize',
        },
        data=audio_bytes,
        timeout=300,  # audios grandes pueden tardar
    )
    upload_resp.raise_for_status()

    file_info = upload_resp.json()
    file_uri  = file_info.get('file', {}).get('uri')
    file_name = file_info.get('file', {}).get('name')

    if not file_uri:
        raise RuntimeError(f"Gemini no retornó file URI. Respuesta: {file_info}")

    log.info("⏳ Audio subido. Esperando procesamiento en Gemini...")
    _wait_for_active(file_name, api_key)
    log.info(f"✅ Audio listo en Gemini: {file_uri}")
    return file_uri, file_name


def _wait_for_active(file_name: str, api_key: str, max_wait: int = 300):
    """Polling hasta que el archivo esté ACTIVE en Gemini."""
    deadline      = time.time() + max_wait
    ultimo_estado = ''

    while time.time() < deadline:
        try:
            resp = requests.get(
                GEMINI_FILES_BASE.format(name=file_name),
                params={'key': api_key},
                timeout=15,
            )
            if resp.status_code == 200:
                state = resp.json().get('state', '')
                if state != ultimo_estado:
                    log.info(f"   Gemini estado: {state}")
                    ultimo_estado = state
                if state == 'ACTIVE':
                    return
                if state == 'FAILED':
                    raise RuntimeError("Gemini FAILED al procesar el audio.")
        except RuntimeError:
            raise
        except Exception as e:
            log.warning(f"   Error en polling: {e}")
        time.sleep(5)

    raise RuntimeError(f"Timeout ({max_wait}s) esperando que Gemini procese el audio.")


def _delete_gemini_file(file_name: str, api_key: str):
    """Elimina el archivo de Gemini para liberar cuota."""
    try:
        requests.delete(
            GEMINI_FILES_BASE.format(name=file_name),
            params={'key': api_key},
            timeout=15,
        )
        log.info(f"🗑️  Archivo Gemini eliminado: {file_name}")
    except Exception as e:
        log.warning(f"No se pudo eliminar archivo Gemini {file_name}: {e}")


def generate_summary(audio_path: Path, reunion_data: dict, gemini_key_info: dict) -> str:
    """
    Genera el resumen ejecutivo de una reunión.

    Args:
        audio_path:      Ruta al archivo final.webm concatenado
        reunion_data:    Datos de la reunión {titulo, descripcion, ...}
        gemini_key_info: Respuesta de get_gemini_key() {api_key, modelo}

    Returns:
        String con el resumen en formato Markdown
    """
    api_key = gemini_key_info['api_key']
    modelo  = gemini_key_info.get('modelo', 'gemini-2.5-flash')
    mime_type = _detect_mime(audio_path)

    file_uri  = None
    file_name = None

    try:
        file_uri, file_name = _upload_audio(audio_path, api_key)

        titulo      = reunion_data.get('titulo', 'Reunión corporativa')
        descripcion = reunion_data.get('descripcion') or 'Sin descripción adicional'

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            titulo=titulo,
            descripcion=descripcion,
        )

        log.info(f"🤖 Generando resumen con {modelo}...")

        payload = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': system_prompt},
                    {'file_data': {'mime_type': mime_type, 'file_uri': file_uri}},
                ],
            }],
            'generationConfig': {
                'temperature':    0.1,
                'maxOutputTokens': 8192,
                # Sin response_mime_type para obtener texto Markdown libre
            },
        }

        resp = requests.post(
            GEMINI_CONTENT_URL.format(model=modelo),
            params={'key': api_key},
            json=payload,
            timeout=300,  # reuniones de 1-2 h pueden tomar tiempo
        )
        resp.raise_for_status()

        content = resp.json()
        texto   = content['candidates'][0]['content']['parts'][0]['text']

        log.info(f"✅ Resumen generado ({len(texto)} caracteres)")
        return texto.strip()

    finally:
        if file_name:
            _delete_gemini_file(file_name, api_key)
