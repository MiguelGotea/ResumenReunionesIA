"""
audio.py — Manejo de fragmentos de audio y concatenación con ffmpeg
"""

import os
import shutil
import subprocess
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
    Concatena todos los fragmentos en orden con ffmpeg.
    Genera: AUDIO_DIR/<reunion_id>/final.webm
    Retorna la ruta del archivo final.
    """
    d = _reunion_dir(reunion_id)

    # Listar fragmentos en orden
    fragments = sorted(d.glob('chunk_*.*'))
    if not fragments:
        raise RuntimeError(f"No hay fragmentos de audio para la reunión {reunion_id}")

    log.info(f"[reunion {reunion_id}] Concatenando {len(fragments)} fragmentos...")

    # Generar concat_list.txt
    concat_file = d / 'concat_list.txt'
    with open(concat_file, 'w') as f:
        for frag in fragments:
            f.write(f"file '{frag.resolve()}'\n")

    final_path = d / 'final.webm'

    cmd = [
        'ffmpeg',
        '-y',                    # sobrescribir si existe
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_file),
        '-c', 'copy',
        str(final_path),
    ]

    log.info(f"[reunion {reunion_id}] ffmpeg: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        log.error(f"[reunion {reunion_id}] ffmpeg stderr: {result.stderr}")
        raise RuntimeError(
            f"ffmpeg falló con código {result.returncode}. "
            f"stderr: {result.stderr[-500:]}"
        )

    size_mb = final_path.stat().st_size / (1024 * 1024)
    log.info(f"[reunion {reunion_id}] Audio concatenado: {final_path} ({size_mb:.1f} MB)")
    return final_path


def delete_audio(reunion_id: int) -> bool:
    """
    Borra la carpeta completa de audio de una reunión.
    Retorna True si se borró, False si no existía.
    """
    d = Path(config.AUDIO_DIR) / str(reunion_id)
    if d.exists():
        shutil.rmtree(d)
        log.info(f"[reunion {reunion_id}] Carpeta de audio eliminada: {d}")
        return True
    log.warning(f"[reunion {reunion_id}] Carpeta de audio no encontrada (ya borrada?): {d}")
    return False


def get_audio_path(reunion_id: int) -> Path | None:
    """Retorna la ruta del final.webm si existe."""
    p = Path(config.AUDIO_DIR) / str(reunion_id) / 'final.webm'
    return p if p.exists() else None
