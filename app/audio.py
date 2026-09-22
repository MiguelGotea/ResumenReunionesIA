"""
audio.py — Manejo de fragmentos de audio y concatenación con ffmpeg
"""

import os
import shutil
from pathlib import Path

from . import config
from .logger import get_logger

log = get_logger('audio')

# Extensiones por mime_type
_MIME_EXT = {
    'audio/webm':       'webm',
    'audio/ogg':        'ogg',
    'audio/mp4':        'mp4',
    'audio/mpeg':       'mp3',
    'video/webm':       'webm',
    'application/octet-stream': 'webm',  # fallback
}

# Formatos que requieren ffmpeg concat (archivos independientes con headers propios).
# WebM de MediaRecorder del browser es un stream continuo → concatenación binaria directa.
# M4A/MP4/AAC del reloj Android son archivos independientes con headers propios → ffmpeg concat.
_FFMPEG_CONCAT_EXTS = {'mp4', 'm4a', 'aac', 'mp3', 'ogg'}


def _reunion_dir(reunion_id: int) -> Path:
    d = Path(config.AUDIO_DIR) / str(reunion_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_fragment(reunion_id: int, chunk_number: int, data: bytes, mime_type: str = 'audio/webm') -> Path:
    """
    Guarda un fragmento de audio en disco.
    Ruta: AUDIO_DIR/<reunion_id>/chunk_<NNN>.<ext>
    """
    ext   = _MIME_EXT.get(mime_type, 'webm')
    fname = f"chunk_{chunk_number:04d}.{ext}"
    path  = _reunion_dir(reunion_id) / fname

    with open(path, 'wb') as f:
        f.write(data)

    log.info(f"[reunion {reunion_id}] Fragmento guardado: {fname} ({len(data) / 1024:.1f} KB)")
    return path


def get_fragment_count(reunion_id: int) -> int:
    """Retorna el número de fragmentos ya guardados."""
    d = Path(config.AUDIO_DIR) / str(reunion_id)
    if not d.exists():
        return 0
    return len(list(d.glob('chunk_*.*')))


def concatenate_fragments(reunion_id: int) -> Path:
    """
    Concatena todos los fragmentos en orden y convierte a MP3.

    Estrategia según el tipo de fragmento (detección automática por extensión):
    - WebM (browser / MediaRecorder web): los chunks son un stream continuo,
      el primer chunk incluye los headers de todo el archivo. Se concatenan
      directamente en binario y luego ffmpeg convierte a MP3.
    - M4A/MP4/AAC (Android nativo / reloj Wear OS): cada chunk es un archivo
      MP4 independiente con sus propios headers moov/mdat. Se usa
      `ffmpeg -f concat` para unirlos correctamente antes de convertir a MP3.

    Genera: AUDIO_DIR/<reunion_id>/final.mp3
    """
    import subprocess

    d = _reunion_dir(reunion_id)

    # Listar fragmentos en orden
    fragments = sorted(d.glob('chunk_*.*'))
    if not fragments:
        raise RuntimeError(f"No hay fragmentos de audio para la reunion {reunion_id}")

    # Determinar estrategia por extension del primer fragmento
    first_ext = fragments[0].suffix.lstrip('.').lower()
    use_ffmpeg_concat = first_ext in _FFMPEG_CONCAT_EXTS

    log.info(
        f"[reunion {reunion_id}] Concatenando {len(fragments)} fragmentos "
        f"(tipo={first_ext}, metodo={'ffmpeg-concat' if use_ffmpeg_concat else 'binario'})..."
    )

    final_path      = d / 'final.mp3'
    final_temp_path = d / f'final_temp.{first_ext}'

    if use_ffmpeg_concat:
        # ── ffmpeg concat para M4A/MP4/AAC (fragmentos independientes) ──────────
        concat_list_path = d / 'concat_list.txt'
        with open(concat_list_path, 'w') as f:
            for frag in fragments:
                escaped = str(frag).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        try:
            subprocess.run(
                [
                    'ffmpeg', '-y',
                    '-f', 'concat', '-safe', '0',
                    '-i', str(concat_list_path),
                    '-c', 'copy',
                    str(final_temp_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            concat_list_path.unlink(missing_ok=True)
        except Exception as e:
            concat_list_path.unlink(missing_ok=True)
            log.error(f"[reunion {reunion_id}] Error en ffmpeg concat: {e}")
            raise RuntimeError(f"Error al concatenar fragmentos M4A con ffmpeg: {e}")

    else:
        # ── Concatenacion binaria para WebM (stream continuo del browser) ───────
        with open(final_temp_path, 'wb') as outfile:
            for frag in fragments:
                with open(frag, 'rb') as infile:
                    outfile.write(infile.read())

    # ── Convertir resultado temporal a MP3 ───────────────────────────────────
    try:
        subprocess.run(
            [
                'ffmpeg', '-y',
                '-i', str(final_temp_path),
                '-c:a', 'libmp3lame', '-b:a', '64k', '-ac', '1', '-ar', '16000',
                str(final_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        final_temp_path.unlink(missing_ok=True)
    except Exception as e:
        log.error(f"[reunion {reunion_id}] Error al convertir a MP3 con ffmpeg: {e}")
        # Fallback: conservar temporal sin conversion
        if final_temp_path.exists():
            final_path = final_temp_path
        else:
            final_path = d / 'final.webm'

    size_mb = final_path.stat().st_size / (1024 * 1024)
    log.info(f"[reunion {reunion_id}] Audio concatenado: {final_path} ({size_mb:.1f} MB)")
    return final_path


def delete_audio(reunion_id: int) -> bool:
    """
    Borra la carpeta completa de audio de una reunion.
    Retorna True si se borro, False si no existia.
    """
    d = Path(config.AUDIO_DIR) / str(reunion_id)
    if d.exists():
        shutil.rmtree(d)
        log.info(f"[reunion {reunion_id}] Carpeta de audio eliminada: {d}")
        return True
    log.warning(f"[reunion {reunion_id}] Carpeta de audio no encontrada (ya borrada?): {d}")
    return False


def get_audio_path(reunion_id: int) -> Path | None:
    """Retorna la ruta del archivo de audio final (mp3 o webm) si existe."""
    d = Path(config.AUDIO_DIR) / str(reunion_id)

    p_mp3 = d / 'final.mp3'
    if p_mp3.exists():
        return p_mp3

    p_webm = d / 'final.webm'
    return p_webm if p_webm.exists() else None
