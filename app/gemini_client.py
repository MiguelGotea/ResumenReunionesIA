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
_PROMPT_RESULTADO_FINAL = """\
Eres un asistente corporativo experto en análisis y documentación de reuniones de negocio.
Has recibido el audio completo de una reunión corporativa de Batidos Pitaya, una cadena de \
batidos y bebidas naturales en Nicaragua.

Contexto de la reunión:
- Título: {titulo}
- Descripción: {descripcion}

Basándote en el audio, genera un resumen ejecutivo corporativo en formato Markdown \
usando EXACTAMENTE estos encabezados:
  ## Decisiones Tomadas
  ## Tareas Asignadas
  ## Acuerdos y Compromisos
  ## Puntos de Seguimiento

En "Tareas Asignadas", indica el responsable si se menciona.
En "Puntos de Seguimiento", incluye fechas límite, riesgos y pendientes.
Si alguna sección no aplica, indícalo (ej: "No se identificaron tareas específicas.").
Usa texto limpio, profesional. NO inventes información que no esté en el audio.
Responde únicamente con el resumen ejecutivo solicitado.
"""

_PROMPT_RESUMEN = """\
Eres un asistente corporativo experto en análisis y documentación de reuniones de negocio.
Has recibido el audio completo de una reunión corporativa de Batidos Pitaya, una cadena de \
batidos y bebidas naturales en Nicaragua.

Contexto de la reunión:
- Título: {titulo}
- Descripción: {descripcion}

Basándote en el audio, genera un resumen general de toda la reunión, documentando \
detalladamente todo lo que se habló, discutió y acordó.
Solo proporciona el texto del resumen general, sin títulos ni formato corporativo adicional.
"""

_TRANSCRIPTION_PROMPT_TEMPLATE = """\
Eres un transcripto profesional experto.
Transcribe palabra por palabra todo lo que se dijo en el audio de esta reunión corporativa.
Incluye el nombre del hablante si se identifica (ej: "Juan: texto...").
Si hay varios hablantes no identificados, usa "Hablante 1", "Hablante 2", etc.
Si hay partes inaudibles, escribe [inaudible].
Si el audio está en silencio o vacío, escribe "[Sin contenido de voz detectado en el audio]".

No agregues comentarios ni introducciones, responde ÚNICAMENTE con la transcripción solicitada.
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
    # Forzamos gemini-1.5-pro para evitar los problemas de truncamiento de flash en audios gigantes
    modelo  = "gemini-1.5-pro"
    mime_type = _detect_mime(audio_path)

    file_uri  = None
    file_name = None

    try:
        file_uri, file_name = _upload_audio(audio_path, api_key)

        titulo      = reunion_data.get('titulo', 'Reunión corporativa')
        descripcion = reunion_data.get('descripcion') or 'Sin descripción adicional'

        # --- 1. Generar Resultado Final ---
        log.info(f"🤖 Generando resultado final (resumen ejecutivo) con {modelo}...")
        prompt_rf = _PROMPT_RESULTADO_FINAL.format(titulo=titulo, descripcion=descripcion)
        payload_rf = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': prompt_rf},
                    {'file_data': {'mime_type': mime_type, 'file_uri': file_uri}},
                ],
            }],
            'generationConfig': {
                'temperature': 0.1,
                'maxOutputTokens': 8192,
            },
        }
        
        resp_rf = requests.post(
            GEMINI_CONTENT_URL.format(model=modelo),
            params={'key': api_key},
            json=payload_rf,
            timeout=300,
        )
        resp_rf.raise_for_status()
        texto_rf = resp_rf.json()['candidates'][0]['content']['parts'][0]['text']
        
        # --- 2. Generar Resumen General ---
        log.info(f"🤖 Generando resumen general con {modelo}...")
        prompt_res = _PROMPT_RESUMEN.format(titulo=titulo, descripcion=descripcion)
        payload_res = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': prompt_res},
                    {'file_data': {'mime_type': mime_type, 'file_uri': file_uri}},
                ],
            }],
            'generationConfig': {
                'temperature': 0.1,
                'maxOutputTokens': 8192,
            },
        }

        resp_res = requests.post(
            GEMINI_CONTENT_URL.format(model=modelo),
            params={'key': api_key},
            json=payload_res,
            timeout=300,
        )
        resp_res.raise_for_status()
        texto_res = resp_res.json()['candidates'][0]['content']['parts'][0]['text']

        log.info("✅ Ambos resúmenes generados con éxito de manera individual.")
        
        resultado = {
            "resultado_final": texto_rf.strip() or "No se pudo generar.",
            "resumen": texto_res.strip() or "No se pudo generar.",
            "transcripcion": ""
        }

        return resultado

    finally:
        if file_name:
            _delete_gemini_file(file_name, api_key)


def generate_transcription(audio_path: Path, gemini_key_info: dict) -> str:
    """
    Genera solo la transcripción del audio.
    """
    api_key = gemini_key_info['api_key']
    # Forzamos gemini-1.5-pro
    modelo  = "gemini-1.5-pro"
    mime_type = _detect_mime(audio_path)

    file_uri  = None
    file_name = None

    try:
        file_uri, file_name = _upload_audio(audio_path, api_key)

        log.info(f"🤖 Generando transcripción con {modelo}...")

        payload = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': _TRANSCRIPTION_PROMPT_TEMPLATE},
                    {'file_data': {'mime_type': mime_type, 'file_uri': file_uri}},
                ],
            }],
            'generationConfig': {
                'temperature':    0.1,
                'maxOutputTokens': 8192,
            },
        }

        resp = requests.post(
            GEMINI_CONTENT_URL.format(model=modelo),
            params={'key': api_key},
            json=payload,
            timeout=400,
        )
        resp.raise_for_status()

        content = resp.json()
        texto   = content['candidates'][0]['content']['parts'][0]['text']

        if content['candidates'][0].get('finishReason') == 'MAX_TOKENS':
            texto += "\n\n[Nota: La transcripción se cortó automáticamente debido al límite de procesamiento de la IA para audios tan largos.]"

        log.info(f"✅ Transcripción generada.")
        return texto

    finally:
        if file_name:
            _delete_gemini_file(file_name, api_key)
