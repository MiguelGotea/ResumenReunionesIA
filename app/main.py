"""
main.py — FastAPI app: endpoints de la API interna + servicio de estáticos

Endpoints:
    POST   /api/fragmento/{token}    Recibe chunk de audio
    POST   /api/estado/{token}       Actualiza estado (grabando/pausada)
    POST   /api/finalizar/{token}    Inicia concatenación + Gemini (background)
    DELETE /api/audio/{reunion_id}   Borra audio físico (llamado desde ERP via API)
    GET    /health                   Health check

La página de grabación (index.html, grabacion.js, estilos.css) se sirve
como archivos estáticos desde app/static/ vía FastAPI StaticFiles.
FastAPI los sirve en la raíz (/), las rutas /api/* van al backend.
"""

import asyncio
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

from . import api_client, audio as audio_module, gemini_client
from . import config
from .tokens import validar_token
from .logger import get_logger

log = get_logger('main')

app = FastAPI(
    title="Resumen de Reuniones IA",
    description="API interna del VPS para grabación y resumen de reuniones con Gemini",
    version="1.0.0",
    docs_url=None,   # Deshabilitar Swagger en producción
    redoc_url=None,
)


# ── Background: procesar audio y llamar a Gemini ──────────────

async def _procesar_reunion_background(token: str, reunion_data: dict, audio_path: Path):
    """
    Tarea de fondo que se ejecuta después de presionar Finalizar:
    1. Actualiza estado: finalizada → procesando
    2. Concatena fragmentos con ffmpeg
    3. Llama a Gemini Files API para generar el resumen
    4. Guarda el resultado en la BD via API
    """
    reunion_id = reunion_data['reunion_id']

    try:
        # finalizada → procesando
        log.info(f"[reunion {reunion_id}] Iniciando procesamiento...")
        await asyncio.to_thread(api_client.actualizar_estado, token, 'procesando')

        # Concatenar fragmentos
        log.info(f"[reunion {reunion_id}] Concatenando fragmentos de audio...")
        final_path = await asyncio.to_thread(audio_module.concatenate_fragments, reunion_id)

        # Obtener key de Gemini
        gemini_key_info = await asyncio.to_thread(api_client.get_gemini_key)

        # Generar resumen con Gemini
        log.info(f"[reunion {reunion_id}] Enviando audio a Gemini ({gemini_key_info['modelo']})...")
        resultado = await asyncio.to_thread(
            gemini_client.generate_summary,
            final_path,
            reunion_data,
            gemini_key_info,
        )

        # Guardar resultado en BD
        ruta_audio = str(final_path)
        await asyncio.to_thread(api_client.guardar_resultado, token, resultado, ruta_audio)
        log.info(f"[reunion {reunion_id}] ✅ Resumen guardado. Estado → completada")

    except Exception as e:
        log.error(f"[reunion {reunion_id}] ❌ Error en procesamiento: {e}")
        # No cambiamos estado automáticamente — queda en 'procesando' para reintento manual
        # en una futura versión se puede agregar un endpoint de reintento


async def _reprocesar_reunion_background(token: str, reunion_data: dict):
    """
    Tarea de fondo para re-procesar una reunión que ya falló o se requiere extraer nuevamente.
    """
    reunion_id = reunion_data['reunion_id']

    try:
        # Estado a procesando
        log.info(f"[reunion {reunion_id}] Iniciando RE-procesamiento IA...")
        await asyncio.to_thread(api_client.actualizar_estado, token, 'procesando')

        # Verificamos si existe el final.webm, si no, intentamos concatenar si hay fragmentos
        final_path = audio_module.get_audio_path(reunion_id)
        if not final_path:
            log.warning(f"[reunion {reunion_id}] final.webm no existe. Intentando re-concatenar fragmentos...")
            final_path = await asyncio.to_thread(audio_module.concatenate_fragments, reunion_id)

        # Obtener key de Gemini
        gemini_key_info = await asyncio.to_thread(api_client.get_gemini_key)

        # Generar resumen con Gemini
        log.info(f"[reunion {reunion_id}] Enviando audio a Gemini ({gemini_key_info['modelo']}) para RE-procesar...")
        resultado = await asyncio.to_thread(
            gemini_client.generate_summary,
            final_path,
            reunion_data,
            gemini_key_info,
        )

        # Guardar resultado en BD
        ruta_audio = str(final_path)
        await asyncio.to_thread(api_client.guardar_resultado, token, resultado, ruta_audio)
        log.info(f"[reunion {reunion_id}] ✅ RE-procesamiento guardado. Estado → completada")

    except Exception as e:
        log.error(f"[reunion {reunion_id}] ❌ Error en RE-procesamiento: {e}")


# ── Endpoints API ─────────────────────────────────────────────

@app.get("/api/info/{token}")
async def info_reunion(token: str):
    """
    Retorna los datos de la reunión (título, descripción, estado, fragment_count)
    sin ningún side effect. Usado por la página de grabación al cargar.
    """
    reunion_data   = validar_token(token)
    fragment_count = audio_module.get_fragment_count(reunion_data['reunion_id'])

    return JSONResponse({
        "success":        True,
        "reunion_id":     reunion_data['reunion_id'],
        "titulo":         reunion_data.get('titulo', ''),
        "descripcion":    reunion_data.get('descripcion', ''),
        "estado":         reunion_data.get('estado', 'creada'),
        "token_expira":   reunion_data.get('token_expira', ''),
        "fragment_count": fragment_count,
    })


@app.post("/api/fragmento/{token}")
async def recibir_fragmento(
    token:        str,
    chunk_number: int        = Form(...),
    file:         UploadFile = File(...),
):
    """Recibe y guarda un fragmento de audio de 60 segundos."""
    reunion_data = validar_token(token)
    reunion_id   = reunion_data['reunion_id']
    estado       = reunion_data['estado']

    # No aceptar fragmentos si ya se finalizó
    if estado in ('finalizada', 'procesando', 'completada', 'cerrada'):
        raise HTTPException(
            status_code=409,
            detail=f"No se puede agregar fragmentos: la reunión está en estado '{estado}'"
        )

    data      = await file.read()
    mime_type = file.content_type or 'audio/webm'

    path = audio_module.save_fragment(reunion_id, chunk_number, data, mime_type)

    # Si es el primer fragmento y la reunión aún está en 'creada' o 'pausada', actualizar estado
    if estado in ('creada', 'pausada'):
        try:
            api_client.actualizar_estado(token, 'grabando')
        except Exception as e:
            log.warning(f"[reunion {reunion_id}] No se pudo actualizar estado a 'grabando': {e}")

    fragment_count = audio_module.get_fragment_count(reunion_id)

    return JSONResponse({
        "success":      True,
        "chunk_saved":  chunk_number,
        "total_chunks": fragment_count,
        "size_bytes":   len(data),
    })


@app.post("/api/estado/{token}")
async def actualizar_estado(token: str, body: dict):
    """Actualiza el estado de la reunión (pausada ↔ grabando)."""
    reunion_data = validar_token(token)
    nuevo_estado = body.get('estado', '')

    if nuevo_estado not in ('grabando', 'pausada'):
        raise HTTPException(
            status_code=422,
            detail=f"Estado inválido: '{nuevo_estado}'. Solo se acepta 'grabando' o 'pausada'."
        )

    # Idempotente: si ya está en ese estado, retornar éxito sin llamar a la API
    if reunion_data.get('estado') == nuevo_estado:
        return JSONResponse({"success": True, "estado": nuevo_estado,
                             "reunion_id": reunion_data['reunion_id'], "idempotent": True})

    try:
        api_client.actualizar_estado(token, nuevo_estado)
        return JSONResponse({"success": True, "estado": nuevo_estado, "reunion_id": reunion_data['reunion_id']})
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/finalizar/{token}")
async def finalizar_reunion(token: str, background_tasks: BackgroundTasks):
    """
    Marca la reunión como finalizada e inicia el procesamiento de Gemini
    en background. Retorna inmediatamente — el cliente NO espera el resumen.
    """
    reunion_data = validar_token(token)
    reunion_id   = reunion_data['reunion_id']
    estado       = reunion_data['estado']

    if estado not in ('creada', 'grabando', 'pausada'):
        raise HTTPException(
            status_code=409,
            detail=f"No se puede finalizar: la reunión está en estado '{estado}'"
        )

    # Actualizar estado: grabando/pausada → finalizada
    try:
        api_client.actualizar_estado(token, 'finalizada')
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Lanzar procesamiento en background
    background_tasks.add_task(
        _procesar_reunion_background,
        token,
        reunion_data,
        None,  # audio_path se calcula dentro del background task
    )

    log.info(f"[reunion {reunion_id}] Finalizada. Procesamiento iniciado en background.")

    return JSONResponse({
        "success": True,
        "message": "Grabación finalizada. El resumen se está procesando y estará disponible en el ERP en breve.",
        "reunion_id": reunion_id,
    })


@app.post("/api/reprocesar/{token}")
async def reprocesar_reunion(
    token: str,
    background_tasks: BackgroundTasks,
    x_resumen_token: str = Header(None, alias="X-Resumen-Token"),
):
    """
    Fuerza el reprocesamiento del audio con Gemini.
    Llamado desde el ERP.
    """
    if not x_resumen_token or x_resumen_token != config.RESUMEN_TOKEN_ERP:
        raise HTTPException(status_code=401, detail="Token no autorizado")

    reunion_data = validar_token(token)
    reunion_id   = reunion_data['reunion_id']
    estado       = reunion_data['estado']

    if estado == 'cerrada' or reunion_data.get('audio_borrado') == 1:
        raise HTTPException(
            status_code=409,
            detail="No se puede reprocesar: la reunión está cerrada o el audio ya fue borrado."
        )

    # Lanzar reprocesamiento en background
    background_tasks.add_task(
        _reprocesar_reunion_background,
        token,
        reunion_data,
    )

    log.info(f"[reunion {reunion_id}] Reprocesamiento forzado iniciado en background.")

    return JSONResponse({
        "success": True,
        "message": "Reprocesamiento IA iniciado.",
        "reunion_id": reunion_id,
    })


@app.delete("/api/audio/{reunion_id}")
async def borrar_audio(
    reunion_id: int,
    x_resumen_token: str = Header(None, alias="X-Resumen-Token"),
):
    """
    Borra el archivo físico de audio de una reunión.
    Llamado desde api.batidospitaya.com/api/resumen_reuniones_ia/aprobar.php
    con el token ERP para verificar la autenticidad.
    """
    # Verificar que el token es el conocido del ERP
    if not x_resumen_token or x_resumen_token != config.RESUMEN_TOKEN_ERP:
        raise HTTPException(status_code=401, detail="Token no autorizado")

    borrado = audio_module.delete_audio(reunion_id)

    return JSONResponse({
        "success":   True,
        "borrado":   borrado,
        "reunion_id": reunion_id,
        "mensaje":   "Audio eliminado" if borrado else "La carpeta de audio no existía (ya fue eliminada)",
    })


@app.get("/api/audio_descarga/{token}")
async def descargar_audio(token: str):
    """
    Exporta el audio de final.webm para que se pueda escuchar/descargar
    por el usuario desde el ERP.
    """
    reunion_data = validar_token(token)
    reunion_id   = reunion_data['reunion_id']
    
    audio_path = audio_module.get_audio_path(reunion_id)
    if not audio_path:
        raise HTTPException(
            status_code=404, 
            detail="Audio no encontrado o ya fue borrado."
        )
        
    return FileResponse(
        audio_path, 
        media_type="audio/webm", 
        filename=f"reunion_{reunion_id}.webm"
    )

@app.get("/health")
async def health():
    """Health check para nginx y monitoreo."""
    return JSONResponse({"status": "ok", "service": "resumen-reuniones-ia"})


# ── Archivos estáticos (página de grabación) ──────────────────
# IMPORTANTE: montar DESPUÉS de los endpoints API para no colisionar
static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
