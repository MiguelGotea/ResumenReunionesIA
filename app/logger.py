"""
logger.py — Logger centralizado con formato legible y salida a stdout
systemd captura stdout/stderr automáticamente con journald.
"""

import logging
import sys

_fmt = logging.Formatter(
    fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
