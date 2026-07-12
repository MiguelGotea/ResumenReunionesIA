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

TIPOS_REUNION_VALIDOS = {'general', 'iterativa', 'informativa', 'decision', 'brainstorming', 'nivel10'}

_BLOQUE_RESOLUCION_TEMPORAL = """
REGLA DE RESOLUCIÓN TEMPORAL:
En esta reunión es posible que un mismo tema se haya discutido múltiples veces cambiando de estado.
La versión y decisión que prevalece es la última que se menciona, anulando cualquier mención o
acuerdo preliminar previo sobre el mismo asunto. Si el tema queda sin resolver al final, asúmelo así.
"""

_BLOQUE_ITERATIVA = """
TIPO DE REUNIÓN: Iterativa / Refinamiento de Prototipo o Proceso.
Las ideas se replantearon repetidamente. Narra la evolución de los temas más importantes (de X a Y)
además de la conclusión final.
"""

_BLOQUE_INFORMATIVA = """
TIPO DE REUNIÓN: Informativa / Reporte de Seguimiento.
Distingue claramente entre decisiones ya tomadas previamente (reportadas) y nuevas decisiones tomadas
"en vivo" durante la reunión, y prioriza resaltar cualquier riesgo o bloqueo mencionado.
"""

_BLOQUE_DECISION = """
TIPO DE REUNIÓN: Toma de Decisión / Comité.
Identifica explícitamente las opciones que se evaluaron, el argumento ganador y quién fue el responsable
final de la decisión.
"""

_BLOQUE_BRAINSTORMING = """
TIPO DE REUNIÓN: Brainstorming / Ideación Abierta.
El objetivo fue la generación de ideas. No es necesario forzar "Decisiones Tomadas" si no las hay.
Asegúrate de agrupar todas las ideas generadas por tema y señala cuáles obtuvieron mayor interés
o tracción, sin inventar un cierre definitivo si no lo hubo.
"""

_BLOQUE_NIVEL10 = """
TIPO DE REUNIÓN: Nivel 10 (Metodología EOS).
Esta reunión tiene una estructura muy rígida. En el resumen, agrupa rigurosamente por los 7 bloques de agenda predefinidos.
- En "Marcador", agrupa los KPIs por participante e indica si están dentro o fuera de objetivo si se menciona.
- En "Issues Resueltos (IDS)", por cada issue tratado, indica el problema identificado, causa raíz, resolución acordada, responsable y fecha límite si existen.
- En "Nuevos Pendientes", formato 'Quién hace qué para cuándo'. No aceptes tareas sin responsable si el audio lo menciona.
Si algún bloque de la agenda no se cubrió en el audio, indícalo expresamente (ej: 'No se registró este bloque en el audio').
"""

_ENCABEZADOS_NIVEL10 = """\
  ## Marcador (KPIs por Persona)
  ## Titulares
  ## Revisión de Rocas
  ## Revisión de Pendientes de la Semana Anterior
  ## Issues Resueltos (IDS)
  ## Nuevos Pendientes (To-Dos)
  ## Calificación de la Reunión\
"""

_ENCABEZADOS_DEFAULT = """\
  ## Decisiones Tomadas
  ## Tareas Asignadas
  ## Acuerdos y Compromisos
  ## Puntos de Seguimiento\
"""

_INSTRUCCIONES_DEFAULT = """\
En "Tareas Asignadas", indica el responsable si se menciona.
En "Puntos de Seguimiento", incluye fechas límite, riesgos y pendientes.
Si alguna sección no aplica, indícalo (ej: "No se identificaron tareas específicas.").
Usa texto limpio, profesional. NO inventes información que no esté en el audio.\
"""

_INSTRUCCIONES_NIVEL10 = """\
Usa texto limpio, profesional. NO inventes información que no esté en el audio.\
"""

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

{bloque_dinamico}

Tu tarea tiene DOS partes, y debes devolver el resultado obligatoriamente usando exactamente \
estas dos etiquetas delimitadoras: <RESULTADO_FINAL> y <RESUMEN>.

─────────────────────────────────────────────────────
ETIQUETA 1: <RESULTADO_FINAL>
─────────────────────────────────────────────────────
Basándote en el audio, genera un resumen ejecutivo corporativo en formato Markdown \
usando EXACTAMENTE estos encabezados (ni más ni menos, mantén exactamente estos nombres):
{encabezados}

{instrucciones_post}

─────────────────────────────────────────────────────
ETIQUETA 2: <RESUMEN>
─────────────────────────────────────────────────────
Un resumen general de toda la reunión, documentando detalladamente todo lo que se habló, \
discutió y acordó, sin títulos ni formato corporativo adicional.

INSTRUCCIONES DE PROFUNDIDAD (obligatorias):
- Prioriza la EXHAUSTIVIDAD sobre la brevedad. No comprimas contenido relevante para ahorrar espacio.
- Para cada decisión, tarea o acuerdo, incluye siempre que esté disponible en el audio:
  · Quién lo propuso (usa "Hablante 1", "Hablante 2", etc. de forma consistente, con
    rol/área entre paréntesis si es inferible del contexto)
  · El contexto o razón detrás de la decisión
  · Cifras, montos, fechas o plazos exactos mencionados
  · Estado: confirmado / pendiente de confirmación / en discusión sin cierre
- Evita frases genéricas tipo "se discutieron varios temas". Nombra explícitamente cada tema.
- Agrega un quinto encabezado:
  ## Temas Discutidos sin Resolución
  (puntos debatidos que no llegaron a una decisión o acuerdo claro — esto es tan importante
  como las decisiones tomadas, no lo omitas aunque parezca secundario)
- Si la reunión abarcó múltiples bloques temáticos, organizá cada sección agrupada por tema.

GESTIÓN DE ESPACIO (crítico para reuniones largas):
Tu respuesta tiene un límite de tokens. Si la reunión es extensa y sentís que no vas a
alcanzar a desarrollar todo con el mismo nivel de detalle:
- NUNCA cortes una etiqueta a mitad de frase o de sección.
- Si debés recortar, hacelo en <RESUMEN>, condensando los tramos menos relevantes,
  pero completa siempre las 5 secciones de <RESULTADO_FINAL> de forma íntegra.

─────────────────────────────────────────────────────
FORMATO DE RESPUESTA (OBLIGATORIO):
─────────────────────────────────────────────────────
Devuelve ÚNICAMENTE las dos etiquetas con su contenido, nada más. No uses bloques de código (```).
Ejemplo:
<RESULTADO_FINAL>
## Decisiones Tomadas...
</RESULTADO_FINAL>
<RESUMEN>
La reunión se centró en...
</RESUMEN>
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
    # Usamos gemini-2.5-pro
    modelo  = "gemini-2.5-pro"
    mime_type = _detect_mime(audio_path)

    file_uri  = None
    file_name = None

    try:
        file_uri, file_name = _upload_audio(audio_path, api_key)

        titulo      = reunion_data.get('titulo', 'Reunión corporativa')
        descripcion = reunion_data.get('descripcion') or 'Sin descripción adicional'
        tipo_reunion = reunion_data.get('tipo_reunion', 'general')

        if tipo_reunion not in TIPOS_REUNION_VALIDOS:
            log.warning(f"Tipo de reunión inválido o desconocido '{tipo_reunion}', haciendo fallback a 'general'")
            tipo_reunion = 'general'

        bloque_dinamico = ""
        encabezados_usar = _ENCABEZADOS_DEFAULT
        instrucciones_post_usar = _INSTRUCCIONES_DEFAULT
        
        # Bloque de resolución temporal a todos menos nivel10
        if tipo_reunion != 'nivel10':
            bloque_dinamico += _BLOQUE_RESOLUCION_TEMPORAL + "\n"
        
        if tipo_reunion == 'iterativa':
            bloque_dinamico += _BLOQUE_ITERATIVA + "\n"
        elif tipo_reunion == 'informativa':
            bloque_dinamico += _BLOQUE_INFORMATIVA + "\n"
        elif tipo_reunion == 'decision':
            bloque_dinamico += _BLOQUE_DECISION + "\n"
        elif tipo_reunion == 'brainstorming':
            bloque_dinamico += _BLOQUE_BRAINSTORMING + "\n"
        elif tipo_reunion == 'nivel10':
            bloque_dinamico += _BLOQUE_NIVEL10 + "\n"
            encabezados_usar = _ENCABEZADOS_NIVEL10
            instrucciones_post_usar = _INSTRUCCIONES_NIVEL10

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            titulo=titulo,
            descripcion=descripcion,
            bloque_dinamico=bloque_dinamico,
            encabezados=encabezados_usar,
            instrucciones_post=instrucciones_post_usar,
        )

        log.info(f"🤖 Generando ambos resúmenes en una sola llamada con {modelo}...")
        payload = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': system_prompt},
                    {'file_data': {'mime_type': mime_type, 'file_uri': file_uri}},
                ],
            }],
            'generationConfig': {
                'temperature': 0.2,
                'maxOutputTokens': 16000,
            },
        }

        resp = requests.post(
            GEMINI_CONTENT_URL.format(model=modelo),
            params={'key': api_key},
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()

        content = resp.json()
        texto = content['candidates'][0]['content']['parts'][0]['text']

        # Extraer usando etiquetas XML
        import re
        
        def extraer_etiqueta(etiqueta, texto_completo):
            # Busca <ETIQUETA> contenido </ETIQUETA> o hasta el final si se truncó
            patron = f"<{etiqueta}>(.*?)(?:</{etiqueta}>|$)"
            match = re.search(patron, texto_completo, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
            return ""

        rf_val = extraer_etiqueta("RESULTADO_FINAL", texto)
        res_val = extraer_etiqueta("RESUMEN", texto)
        
        if not rf_val and not res_val:
            log.warning(f"No se encontraron etiquetas XML. Fallback a texto crudo. Longitud: {len(texto)}")
            rf_val = texto
            res_val = ""
        else:
            if content['candidates'][0].get('finishReason') == 'MAX_TOKENS':
                log.warning(f"Respuesta truncada por MAX_TOKENS (longitud {len(texto)}).")
            else:
                log.info(f"Longitud de salida: {len(texto)} caracteres.")

            log.info("✅ Resumen generado y validado con etiquetas XML.")
        
        resultado = {
            "resultado_final": rf_val or "No se pudo generar.",
            "resumen": res_val or "No se pudo generar.",
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
    # Usamos gemini-2.5-pro
    modelo  = "gemini-2.5-pro"
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
