/**
 * grabacion.js — Lógica de grabación de audio
 *
 * Flujo:
 *   1. Al cargar la página: extrae token de URL, llama GET /api/info/{token}
 *   2. Renderiza estado inicial según el estado actual de la reunión
 *   3. Empezar: getUserMedia → MediaRecorder → start(60000ms timeslice)
 *   4. Cada 60s: ondataavailable → POST /api/fragmento/{token}
 *   5. Pausa/Reanudar: mediaRecorder.pause()/resume() + notify API
 *   6. Finalizar: stop() → espera último chunk → POST /api/finalizar/{token}
 */

'use strict';

// ── Constantes ────────────────────────────────────────────────
const CHUNK_INTERVAL_MS = 60_000;  // 60 segundos por fragmento
const MAX_RETRIES       = 3;
const RETRY_DELAY_MS    = 2_000;

// ── Estado global ─────────────────────────────────────────────
let token          = null;
let reunionData    = null;
let mediaRecorder  = null;
let stream         = null;
let chunkCounter   = 0;
let timerInterval  = null;
let elapsedSeconds = 0;
let finalizando    = false;

// ── Único punto de inicialización ─────────────────────────────
window.addEventListener('load', async () => {
    token = new URLSearchParams(window.location.search).get('token');

    if (!token || token.length < 40) {
        mostrarEstado('sinToken');
        return;
    }

    mostrarEstado('cargando');

    try {
        const resp = await fetch(`/api/info/${token}`);
        const data = await resp.json();

        if (!resp.ok) {
            if (resp.status === 403) { mostrarError('Token expirado', data.detail || 'El tiempo límite de esta reunión (6 horas) ha vencido.'); return; }
            if (resp.status === 410) { mostrarFinalizado('Reunión cerrada', data.detail || 'Esta reunión fue aprobada y el acceso ha sido revocado.', 'bi-lock-fill'); return; }
            mostrarError('Acceso no válido', data.detail || 'El token no es válido o no existe.');
            return;
        }

        reunionData  = data;
        chunkCounter = data.fragment_count || 0;
        const estado = data.estado || 'creada';

        // Estados terminales
        if (['finalizada', 'procesando'].includes(estado)) {
            mostrarFinalizado(
                'Procesando resumen…',
                'La grabación fue finalizada. El resumen estará disponible en el ERP en unos minutos.',
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
        mostrarError('Error de conexión', 'No se pudo conectar al servidor. Verifica tu conexión a internet.');
        log(`Error: ${e.message}`, 'err');
    }
});


// ── Inicializar panel ─────────────────────────────────────────

function inicializarPanelGrabacion(data, estado) {
    document.getElementById('reunionTitulo').textContent      = data.titulo      || 'Reunión';
    document.getElementById('reunionDescripcion').textContent = data.descripcion || '';

    mostrarEstado('grabacion');
    actualizarChunksInfo();

    if (estado === 'grabando' || estado === 'pausada') {
        // Reconexión: sesión previa activa
        log(`⚠️ Reconexión — sesión anterior en estado '${estado}'`, 'warn');
        log(`📦 ${chunkCounter} fragmento(s) ya guardados en el servidor`, 'ok');
        setEstadoUI('pausada');   // Mostramos Reanudar (no podemos retomar el stream sin getUserMedia)
        setStatusDot('pausada');
        setStatusText('Sesión interrumpida — presiona Reanudar');
    } else {
        // Estado creada — inicio fresco
        setEstadoUI('inicio');
        setStatusDot('listo');
        setStatusText('Listo para iniciar');
    }
}


// ── Acciones de control ───────────────────────────────────────

async function accionEmpezar() {
    log('🎤 Solicitando acceso al micrófono…');
    setBotonEstado('btnEmpezar', true);   // solo deshabilitar el botón que se presionó

    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (e) {
        setBotonEstado('btnEmpezar', false);
        log(`❌ Sin acceso al micrófono: ${e.message}`, 'err');
        alert(`No se pudo acceder al micrófono:\n${e.message}`);
        return;
    }

    const mimeType = getSupportedMime();
    log(`🎵 Codec: ${mimeType || 'predeterminado del navegador'}`);

    const opts = mimeType ? { mimeType } : {};
    mediaRecorder = new MediaRecorder(stream, opts);

    mediaRecorder.addEventListener('dataavailable', async (e) => {
        if (e.data && e.data.size > 100) {   // ignorar chunks vacíos (< 100 bytes)
            const n = ++chunkCounter;
            actualizarChunksInfo();
            log(`📤 Enviando fragmento #${n} (${(e.data.size / 1024).toFixed(0)} KB)…`);
            const ok = await enviarChunkConReintentos(e.data, n, mimeType);
            if (ok) log(`✅ Fragmento #${n} guardado`, 'ok');
        }
    });

    mediaRecorder.addEventListener('stop', async () => {
        if (finalizando) {
            await completarFinalizacion();
        }
    });

    mediaRecorder.start(CHUNK_INTERVAL_MS);

    // Notificar estado a la API
    try {
        await postEstado('grabando');
    } catch (e) {
        log(`⚠️ No se pudo notificar inicio al servidor: ${e.message}`, 'warn');
    }

    // Actualizar UI — aquí ya no hay botones deshabilitados
    setEstadoUI('grabando');
    setStatusDot('grabando');
    setStatusText('Grabando…');
    iniciarTimer();
    log('🔴 Grabación iniciada', 'ok');
}

async function accionPausa() {
    if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
    mediaRecorder.pause();

    try { await postEstado('pausada'); }
    catch (e) { log(`⚠️ No se pudo notificar pausa: ${e.message}`, 'warn'); }

    setEstadoUI('pausada');
    setStatusDot('pausada');
    setStatusText('Pausada');
    detenerTimer();
    log('⏸️ Grabación pausada');
}

async function accionReanudar() {
    // Si el stream fue cortado (reconexión de navegador), pedir micrófono nuevo
    const streamCortado = !stream || stream.getTracks().every(t => t.readyState === 'ended');
    if (streamCortado || !mediaRecorder || mediaRecorder.state === 'inactive') {
        log('🔄 Reconectando — solicitando micrófono…');
        await accionEmpezar();
        return;
    }

    if (mediaRecorder.state === 'paused') {
        mediaRecorder.resume();
    }

    try { await postEstado('grabando'); }
    catch (e) { log(`⚠️ No se pudo notificar reanudación: ${e.message}`, 'warn'); }

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
    deshabilitarTodos(true);
    setStatusText('Finalizando…');
    log('⏹️ Finalizando grabación…');

    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        // El evento 'stop' llamará completarFinalizacion()
        mediaRecorder.stop();
    } else {
        await completarFinalizacion();
    }
}

async function completarFinalizacion() {
    detenerTimer();

    // Detener tracks del micrófono
    if (stream) {
        stream.getTracks().forEach(t => t.stop());
    }

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
            alert('Hubo un error al finalizar. Intenta cerrar y volver a abrir la página.');
            finalizando = false;
            deshabilitarTodos(false);
            setEstadoUI('pausada');
        }
    } catch (e) {
        log(`❌ Error de red al finalizar: ${e.message}`, 'err');
        alert('Error de conexión al finalizar. Intenta de nuevo.');
        finalizando = false;
        deshabilitarTodos(false);
    }
}


// ── Envío de chunks con reintentos ────────────────────────────

async function enviarChunkConReintentos(blob, chunkNumber, mimeType, intento = 0) {
    const formData = new FormData();
    formData.append('chunk_number', String(chunkNumber));
    formData.append('file', blob, `chunk_${chunkNumber}.webm`);

    try {
        const resp = await fetch(`/api/fragmento/${token}`, {
            method: 'POST',
            body:   formData,
        });

        if (resp.ok) return true;

        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
    } catch (e) {
        if (intento < MAX_RETRIES) {
            log(`⚠️ Fragmento #${chunkNumber} falló (intento ${intento + 1}/${MAX_RETRIES}): ${e.message}`, 'warn');
            await sleep(RETRY_DELAY_MS);
            return enviarChunkConReintentos(blob, chunkNumber, mimeType, intento + 1);
        }
        log(`❌ Fragmento #${chunkNumber} perdido tras ${MAX_RETRIES} intentos: ${e.message}`, 'err');
        return false;
    }
}

async function postEstado(estado) {
    const resp = await fetch(`/api/estado/${token}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ estado }),
    });
    if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
}


// ── UI helpers ────────────────────────────────────────────────

function mostrarEstado(cual) {
    const ids = ['estadoSinToken', 'estadoError', 'estadoCargando', 'estadoFinalizado', 'panelGrabacion'];
    ids.forEach(id => document.getElementById(id).classList.add('d-none'));
    const map = {
        sinToken:   'estadoSinToken',
        error:      'estadoError',
        cargando:   'estadoCargando',
        finalizado: 'estadoFinalizado',
        grabacion:  'panelGrabacion',
    };
    if (map[cual]) document.getElementById(map[cual]).classList.remove('d-none');
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
    const todos = ['btnEmpezar', 'btnPausa', 'btnReanudar', 'btnFinalizar'];
    todos.forEach(id => document.getElementById(id).classList.add('d-none'));

    if (estado === 'inicio') {
        show('btnEmpezar');
    } else if (estado === 'grabando') {
        show('btnPausa');
        show('btnFinalizar');
    } else if (estado === 'pausada') {
        show('btnReanudar');
        show('btnFinalizar');
    }

    // Siempre re-habilitar al cambiar de estado
    todos.forEach(id => setBotonEstado(id, false));
}

function show(id) {
    document.getElementById(id).classList.remove('d-none');
}

function setBotonEstado(id, disabled) {
    document.getElementById(id).disabled = disabled;
}

function deshabilitarTodos(disabled) {
    ['btnEmpezar', 'btnPausa', 'btnReanudar', 'btnFinalizar'].forEach(id => {
        setBotonEstado(id, disabled);
    });
}

function setStatusText(texto) {
    document.getElementById('statusText').textContent = texto;
}

function setStatusDot(estado) {
    const dot = document.getElementById('statusDot');
    dot.className = 'status-dot';
    if (['grabando', 'pausada', 'listo', 'completado'].includes(estado)) dot.classList.add(estado);
}

function actualizarChunksInfo() {
    const el = document.getElementById('chunksInfo');
    document.getElementById('chunksText').textContent =
        `${chunkCounter} fragmento(s) guardado(s) en el servidor`;
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
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
}

function formatTime(s) {
    const h   = Math.floor(s / 3600).toString().padStart(2, '0');
    const m   = Math.floor((s % 3600) / 60).toString().padStart(2, '0');
    const sec = (s % 60).toString().padStart(2, '0');
    return `${h}:${m}:${sec}`;
}


// ── Log de actividad ──────────────────────────────────────────

function log(msg, tipo = '') {
    const container = document.getElementById('activityLog');
    const entry     = document.createElement('div');
    entry.className = `log-entry log-${tipo}`;
    const time      = new Date().toTimeString().slice(0, 8);
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
