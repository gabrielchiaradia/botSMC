"""
utils/logger.py
───────────────
Logger centralizado con salida a consola (colorida) y archivo.
Todos los módulos importan get_logger(__name__) desde aquí.
"""

import logging
import sys
from pathlib import Path
from config.settings import logs as lcfg


def get_logger(name: str) -> logging.Logger:
    """Retorna un logger configurado para el módulo dado."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # Ya configurado

    logger.setLevel(getattr(logging, lcfg.LEVEL, logging.INFO))

    fmt = logging.Formatter(
        fmt   = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
    )

    # ── Handler consola ───────────────────────────────────
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(ColorFormatter())
    logger.addHandler(sh)

    # ── Handler archivo ───────────────────────────────────
    try:
        lcfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(lcfg.LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # Si no se puede escribir archivo, solo consola

    return logger


class ColorFormatter(logging.Formatter):
    """Formateador con colores ANSI para la consola."""

    COLORS = {
        logging.DEBUG:    "\033[36m",    # Cyan
        logging.INFO:     "\033[32m",    # Verde
        logging.WARNING:  "\033[33m",    # Amarillo
        logging.ERROR:    "\033[31m",    # Rojo
        logging.CRITICAL: "\033[35m",    # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        record.levelname = f"{color}{record.levelname:8}{self.RESET}"
        record.name      = f"\033[2m{record.name}\033[0m"
        return super().format(record)

    def __init__(self):
        super().__init__(
            fmt     = "%(asctime)s %(levelname)s %(name)s — %(message)s",
            datefmt = "%H:%M:%S",
        )
