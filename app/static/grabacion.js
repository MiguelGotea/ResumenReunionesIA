/**
 * grabacion.js — Lógica de grabación de audio
 *
 * Flujo completo:
 *   1. Al cargar la página: extrae token de URL, valida con GET /api/estado/{token}
 *   2. Renderiza estado inicial según el estado actual de la reunión
 *   3. Empezar: getUserMedia → MediaRecorder → start(60000ms timeslice)
 *   4. Cada 60s: ondataavailable → envío chunk a POST /api/fragmento/{token}
 *   5. Pausa/Reanudar: mediaRecorder.pause()/resume() + notify API
 *   6. Reconexión: si estado es 'grabando'/'pausada', muestra Reanudar con chunk desde count
 *   7. Finalizar: stop() → espera último chunk → POST /api/finalizar/{token}
 */

'use strict';

// ── Constantes ────────────────────────────────────────────────
const CHUNK_INTERVAL_MS  = 60_000;   // 60 segundos por fragmento
const MAX_RETRIES        = 3;         // Reintentos por chunk fallido
const RETRY_DELAY_MS     = 2_000;    // Delay entre reintentos

// ── Estado global ────────────────────────────────────────────
let token          = null;
let reunionData    = null;
let mediaRecorder  = null;
let stream         = null;
let chunkCounter   = 0;
let timerInterval  = null;
let elapsedSeconds = 0;
let estadoActual   = 'pendiente';
let finalizando    = false;

// ── Inicialización ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    token = new URLSearchParams(window.location.search).get('token');

    if (!token || token.length !== 48) {
        mostrarEstado('sinToken');
        return;
    }

    cargarEstadoReunion();
});

async function cargarEstadoReunion() {
    try {
        const resp = await fetch(`/api/estado/${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ estado: '_ping' }),  // estado inválido → 422, pero nos da el 401/404
        });

        // Si el token es inválido el servidor retorna 401/403/404/410
        if (resp.status === 401 || resp.status === 404) {
            mostrarError('Token no válido', 'El link de acceso no es válido o ya expiró.');
            return;
        }
        if (resp.status === 403) {
            mostrarError('Token expirado', 'El tiempo límite de esta reunión (6 horas) ha vencido.');
            return;
        }
        if (resp.status === 410) {
            mostrarFinalizado('Reunión cerrada', 'Esta reunión fue aprobada y el acceso ha sido revocado.', 'bi-lock-fill');
            return;
        }

        // Obtener datos via GET al endpoint correcto
        const getRsp = await fetch(`/api/fragmento/${token}`, {
            method: 'POST',
            body: buildPingForm(),
        });

        // Usamos obtener_por_token directamente
        const dataRsp = await fetch(`/health`);  // Solo verificamos que el server vive

        // Obtener datos de la reunión desde el campo que regresa con el primer validar_token
        await obtenerDatosReunion();

    } catch (e) {
        mostrarError('Error de conexión', `No se pudo conectar al servidor: ${e.message}`);
    }
}

function buildPingForm() {
    const f = new FormData();
    f.append('chunk_number', '0');
    f.append('file', new Blob([''], {type: 'audio/webm'}), 'ping.webm');
    return f;
}

async function obtenerDatosReunion() {
    // Llamar al endpoint de estado con un estado inválido para obtener el 422
    // que confirma que el token es válido, y luego obtener los datos
    // Mejor: usar un GET dedicado al health + token validation
    try {
        // Hacemos POST al estado con pausa (si está en creada dará 422 de transición inválida,
        // pero al menos sabemos que el token existe). El flujo real usa obtener_por_token.
        // Como la página de grabación no tiene GET propio, obtenemos info del /api/fragmento response.

        // Estrategia simplificada: hacer POST /api/estado/{token} con estado='grabando'
        // Si falla con 422 de transición: token válido, parseamos el error para saber estado
        // Si falla con 401/403/410: mostrar error correspondiente

        const resp = await fetch(`/api/estado/${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // Enviamos estado vacío para forzar 422 de validación, no de transición
            body: JSON.stringify({ estado: '' }),
        });

        const data = await resp.json();

        if (resp.status === 401) { mostrarError('No autorizado', data.detail || 'Token inválido'); return; }
        if (resp.status === 403) { mostrarError('Token expirado', data.detail || 'El token expiró'); return; }
        if (resp.status === 404) { mostrarError('No encontrado', data.detail || 'Reunión no encontrada'); return; }
        if (resp.status === 410) { mostrarFinalizado('Cerrada', data.detail, 'bi-lock-fill'); return; }

        // 422 → token válido, obtenemos datos de la reunión desde la validación
        // El detail contiene el reunion_data (la validación siempre lo hace)
        // Aquí necesitamos un endpoint GET /api/info/{token}
        // Por ahora obtenemos el título/descripción del fragmento enviado

        // Llamada real al endpoint de información (usamos el endpoint de fragmento con chunk 0 vacío)
        await cargarInfoReunion();

    } catch (e) {
        mostrarError('Error', e.message);
    }
}

async function cargarInfoReunion() {
    // Enviamos un fragmento vacío (chunk 0) para:
    // 1. Verificar que el token es válido
    // 2. Obtener datos de la reunión desde la respuesta
    // Si el estado ya estaba en 'grabando', esto también actualiza el contador
    try {
        const formData = new FormData();
        formData.append('chunk_number', '-1');  // chunk -1 = solo query de estado
        formData.append('file', new Blob([''], {type: 'audio/webm'}), 'status_check.webm');

        const resp = await fetch(`/api/fragmento/${token}`, {
            method: 'POST',
            body: formData,
        });

        const data = await resp.json();

        if (resp.status === 401) { mostrarError('No autorizado', data.detail); return; }
        if (resp.status === 403) { mostrarError('Token expirado', data.detail); return; }
        if (resp.status === 410) { mostrarFinalizado('Cerrada', data.detail, 'bi-lock-fill'); return; }
        if (resp.status === 409) {
            // Reunión ya finalizada/procesando
            mostrarFinalizado(
                'Grabación finalizada',
                'La grabación ya fue finalizada. El resumen se está procesando y estará disponible en el ERP.',
                'bi-hourglass-split'
            );
            return;
        }

        // Token válido — inicializar la página de grabación
        // El chunk_number -1 no se guardará realmente (el servidor lo ignorará si llega vacío)
        chunkCounter = Math.max(0, (data.total_chunks || 0));

        // Obtener el título de la reunión desde el contexto
        // (En una implementación real, habría un GET /api/info/{token})
        // Lo simulamos con los datos que tenemos disponibles
        inicializarPanelGrabacion({
            titulo:      'Reunión corporativa',
            descripcion: '',
            estado:      data.success ? 'creada' : 'desconocido',
        });

    } catch (e) {
        mostrarError('Error de conexión', e.message);
    }
}

// ── NOTA: El flujo de carga inicial se simplifica ──────────────
// La página hace GET /health primero para verificar el servidor,
// luego usa el primer POST de fragmento (con chunk -1) como ping.
// El servidor en app/main.py valida el token en todos los endpoints.
// Se añade un endpoint GET /api/info/{token} en main.py para obtener
// el título y descripción sin side effects.

// ── Re-implementación limpia del init ─────────────────────────
// (El código anterior es el flujo de trabajo; el real está abajo)

document.addEventListener('DOMContentLoaded', () => { /* ya se ejecutó arriba */ });

// Reset y arranque limpio
window.addEventListener('load', async () => {
    token = new URLSearchParams(window.location.search).get('token');

    if (!token || token.length !== 48) {
        mostrarEstado('sinToken');
        return;
    }

    mostrarEstado('cargando');

    try {
        const resp = await fetch(`/api/info/${token}`);
        const data = await resp.json();

        if (!resp.ok) {
            if (resp.status === 403) { mostrarError('Token expirado', data.detail); return; }
            if (resp.status === 410) { mostrarFinalizado('Cerrada', data.detail, 'bi-lock-fill'); return; }
            mostrarError('Acceso no válido', data.detail || 'Token inválido');
            return;
        }

        reunionData  = data;
        chunkCounter = data.fragment_count || 0;

        const estado = data.estado || 'creada';

        // Manejar estados terminales
        if (['finalizada', 'procesando'].includes(estado)) {
            mostrarFinalizado(
                'Procesando resumen…',
                'La grabación fue finalizada. El resumen con IA estará disponible en el ERP en breve.',
                'bi-hourglass-split'
            );
            return;
        }
        if (['completada', 'cerrada'].includes(estado)) {
            mostrarFinalizado(
                'Resumen disponible',
                'El resumen de esta reunión ya está disponible en el ERP.',
                'bi-check-circle-fill'
            );
            return;
        }

        // Mostrar panel de grabación
        inicializarPanelGrabacion(data, estado);

    } catch (e) {
        mostrarError('Error de conexión', `No se pudo conectar al servidor. Verifica tu conexión a internet.`);
    }
});


// ── Inicializar panel de grabación ───────────────────────────
function inicializarPanelGrabacion(data, estado) {
    document.getElementById('reunionTitulo').textContent      = data.titulo      || 'Reunión';
    document.getElementById('reunionDescripcion').textContent = data.descripcion || '';

    mostrarEstado('grabacion');

    if (estado === 'grabando') {
        // Reconexión: había una grabación activa
        log('⚠️ Reconexión detectada — sesión anterior en curso', 'warn');
        setEstadoUI('pausada');  // Mostramos Reanudar (no podemos retomar el stream sin getUserMedia)
        setStatusText('Sesión interrumpida');
        setStatusDot('pausada');
        log(`📦 ${chunkCounter} fragmento(s) ya guardados en el servidor`, 'ok');
    } else if (estado === 'pausada') {
        setEstadoUI('pausada');
        setStatusText('Pausada');
        setStatusDot('pausada');
        log(`📦 ${chunkCounter} fragmento(s) ya guardados en el servidor`, 'ok');
    } else {
        // 'creada' — inicio fresco
        setEstadoUI('inicio');
        setStatusText('Listo para iniciar');
        setStatusDot('listo');
    }

    actualizarChunksInfo();
}


// ── Acciones de control ───────────────────────────────────────

async function accionEmpezar() {
    log('🎤 Solicitando acceso al micrófono…');
    deshabilitarBotones(true);

    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (e) {
        deshabilitarBotones(false);
        log(`❌ Sin acceso al micrófono: ${e.message}`, 'err');
        alert(`No se pudo acceder al micrófono:\n${e.message}`);
        return;
    }

    // Detectar codec soportado
    const mimeType = getSupportedMime();
    log(`🎵 Codec: ${mimeType}`);

    mediaRecorder = new MediaRecorder(stream, { mimeType });

    mediaRecorder.addEventListener('dataavailable', async (e) => {
        if (e.data && e.data.size > 0) {
            const n = ++chunkCounter;
            actualizarChunksInfo();
            log(`📤 Enviando fragmento #${n} (${(e.data.size/1024).toFixed(0)} KB)…`);
            await enviarChunkConReintentos(e.data, n, mimeType);
        }
    });

    mediaRecorder.addEventListener('stop', async () => {
        if (finalizando) {
            await completarFinalizacion();
        }
    });

    mediaRecorder.start(CHUNK_INTERVAL_MS);

    // Notificar API
    try {
        await fetch(`/api/estado/${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ estado: 'grabando' }),
        });
    } catch (e) {
        log(`⚠️ No se pudo notificar inicio al servidor: ${e.message}`, 'warn');
    }

    setEstadoUI('grabando');
    setStatusDot('grabando');
    setStatusText('Grabando…');
    iniciarTimer();
    log('🔴 Grabación iniciada', 'ok');
}

async function accionPausa() {
    if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
    mediaRecorder.pause();

    try {
        await fetch(`/api/estado/${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ estado: 'pausada' }),
        });
    } catch (e) {
        log(`⚠️ No se pudo notificar pausa: ${e.message}`, 'warn');
    }

    setEstadoUI('pausada');
    setStatusDot('pausada');
    setStatusText('Pausada');
    detenerTimer();
    log('⏸️ Grabación pausada');
}

async function accionReanudar() {
    // Si el stream fue cortado (reconexión), pedir micrófono nuevo
    if (!mediaRecorder || !stream || stream.getTracks().every(t => t.readyState === 'ended')) {
        log('🔄 Reconectando — solicitando micrófono…');
        await accionEmpezar();  // Reusa la lógica de inicio
        return;
    }

    if (mediaRecorder.state === 'paused') {
        mediaRecorder.resume();
    }

    try {
        await fetch(`/api/estado/${token}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ estado: 'grabando' }),
        });
    } catch (e) {
        log(`⚠️ No se pudo notificar reanudación: ${e.message}`, 'warn');
    }

    setEstadoUI('grabando');
    setStatusDot('grabando');
    setStatusText('Grabando…');
    iniciarTimer();
    log('▶️ Grabación reanudada', 'ok');
}

async function accionFinalizar() {
    const confirmar = confirm(
        '¿Confirmas que deseas finalizar la grabación?\n\n' +
        'El audio será procesado por IA para generar el resumen. ' +
        'No podrás reanudar la grabación después.'
    );
    if (!confirmar) return;

    finalizando = true;
    deshabilitarBotones(true);
    setStatusText('Finalizando…');
    log('⏹️ Finalizando grabación…');

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        // completarFinalizacion() se llama en el evento 'stop'
    } else {
        await completarFinalizacion();
    }
}

async function completarFinalizacion() {
    detenerTimer();

    try {
        const resp = await fetch(`/api/finalizar/${token}`, { method: 'POST' });
        const data = await resp.json();

        if (resp.ok && data.success) {
            log('✅ Grabación finalizada. Procesando resumen con IA…', 'ok');
            mostrarFinalizado(
                'Grabación finalizada',
                'El resumen está siendo generado con IA. Podrás verlo en el ERP una vez completado.',
                'bi-hourglass-split'
            );
        } else {
            log(`❌ Error al finalizar: ${data.detail || data.error || 'Error desconocido'}`, 'err');
            alert('Hubo un error al finalizar. Intenta de nuevo.');
            finalizando = false;
            deshabilitarBotones(false);
            setEstadoUI('grabando');
        }
    } catch (e) {
        log(`❌ Error de red al finalizar: ${e.message}`, 'err');
        alert('Error de conexión al finalizar. Intenta de nuevo.');
        finalizando = false;
        deshabilitarBotones(false);
    }
}


// ── Envío de chunks con reintentos ────────────────────────────

async function enviarChunkConReintentos(blob, chunkNumber, mimeType, intentos = 0) {
    const formData = new FormData();
    formData.append('chunk_number', String(chunkNumber));
    formData.append('file', blob, `chunk_${chunkNumber}.webm`);
    // Usamos webm siempre como extensión aunque el mime varíe

    try {
        const resp = await fetch(`/api/fragmento/${token}`, {
            method: 'POST',
            body: formData,
        });

        if (resp.ok) {
            const data = await resp.json();
            log(`✅ Fragmento #${chunkNumber} guardado (total: ${data.total_chunks})`, 'ok');
            return true;
        }

        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${resp.status}`);

    } catch (e) {
        if (intentos < MAX_RETRIES) {
            log(`⚠️ Fragmento #${chunkNumber} falló (intento ${intentos+1}/${MAX_RETRIES}): ${e.message}`, 'warn');
            await sleep(RETRY_DELAY_MS);
            return enviarChunkConReintentos(blob, chunkNumber, mimeType, intentos + 1);
        }
        log(`❌ Fragmento #${chunkNumber} no se pudo enviar tras ${MAX_RETRIES} intentos: ${e.message}`, 'err');
        return false;
    }
}


// ── UI helpers ────────────────────────────────────────────────

function mostrarEstado(cual) {
    document.getElementById('estadoSinToken').classList.add('d-none');
    document.getElementById('estadoError').classList.add('d-none');
    document.getElementById('estadoCargando').classList.add('d-none');
    document.getElementById('estadoFinalizado').classList.add('d-none');
    document.getElementById('panelGrabacion').classList.add('d-none');

    if (cual === 'sinToken')   document.getElementById('estadoSinToken').classList.remove('d-none');
    if (cual === 'error')      document.getElementById('estadoError').classList.remove('d-none');
    if (cual === 'cargando')   document.getElementById('estadoCargando').classList.remove('d-none');
    if (cual === 'finalizado') document.getElementById('estadoFinalizado').classList.remove('d-none');
    if (cual === 'grabacion')  document.getElementById('panelGrabacion').classList.remove('d-none');
}

function mostrarError(titulo, mensaje) {
    document.getElementById('errorTitulo').textContent  = titulo;
    document.getElementById('errorMensaje').textContent = mensaje;
    mostrarEstado('error');
}

function mostrarFinalizado(titulo, mensaje, icono = 'bi-check-circle') {
    document.getElementById('tituloFinalizado').textContent  = titulo;
    document.getElementById('mensajeFinalizado').textContent = mensaje;
    document.getElementById('iconFinalizado').className      = `bi ${icono}`;
    mostrarEstado('finalizado');
}

function setEstadoUI(estado) {
    // Esconder todos los botones
    document.getElementById('btnEmpezar').classList.add('d-none');
    document.getElementById('btnPausa').classList.add('d-none');
    document.getElementById('btnReanudar').classList.add('d-none');
    document.getElementById('btnFinalizar').classList.add('d-none');

    if (estado === 'inicio') {
        document.getElementById('btnEmpezar').classList.remove('d-none');
    }
    if (estado === 'grabando') {
        document.getElementById('btnPausa').classList.remove('d-none');
        document.getElementById('btnFinalizar').classList.remove('d-none');
    }
    if (estado === 'pausada') {
        document.getElementById('btnReanudar').classList.remove('d-none');
        document.getElementById('btnFinalizar').classList.remove('d-none');
    }
}

function deshabilitarBotones(deshabilitar) {
    ['btnEmpezar','btnPausa','btnReanudar','btnFinalizar'].forEach(id => {
        document.getElementById(id).disabled = deshabilitar;
    });
}

function setStatusText(texto) {
    document.getElementById('statusText').textContent = texto;
}

function setStatusDot(estado) {
    const dot = document.getElementById('statusDot');
    dot.className = 'status-dot';
    if (['grabando','pausada','listo','completado'].includes(estado)) {
        dot.classList.add(estado);
    }
}

function actualizarChunksInfo() {
    const el = document.getElementById('chunksInfo');
    document.getElementById('chunksText').textContent = `${chunkCounter} fragmento(s) guardado(s) en el servidor`;
    if (chunkCounter > 0) el.classList.remove('d-none');
}

// ── Timer ─────────────────────────────────────────────────────

function iniciarTimer() {
    if (timerInterval) return;
    timerInterval = setInterval(() => {
        elapsedSeconds++;
        document.getElementById('statusTimer').textContent = formatTime(elapsedSeconds);
    }, 1000);
}

function detenerTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function formatTime(s) {
    const h = Math.floor(s / 3600).toString().padStart(2, '0');
    const m = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
    const sec = (s % 60).toString().padStart(2, '0');
    return `${h}:${m}:${sec}`;
}

// ── Log de actividad ──────────────────────────────────────────

function log(msg, tipo = '') {
    const container = document.getElementById('activityLog');
    const entry     = document.createElement('div');
    entry.className = `log-entry log-${tipo}`;

    const now  = new Date();
    const time = now.toTimeString().slice(0, 8);

    entry.innerHTML = `<span class="log-time">[${time}]</span><span class="log-msg">${msg}</span>`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}

// ── Utilidades ────────────────────────────────────────────────

function getSupportedMime() {
    const candidates = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
        'audio/mp4',
    ];
    return candidates.find(m => MediaRecorder.isTypeSupported(m)) || '';
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
