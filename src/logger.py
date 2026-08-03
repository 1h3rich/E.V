"""
Modulo: logger.py
-------------------
Registro de actividad de E.V: llamadas a herramientas, confirmaciones de
acciones peligrosas y errores. Se guarda en data/logs/ivy.log (con rotacion)
y ademas sale por stdout/stderr, que systemd captura en el journal
(journalctl --user -u ivy).

Ademas, cada turno de conversacion (lo que dice el usuario y lo que
responde E.V) se guarda sin rotar en data/logs/conversaciones.log: es una
transcripcion permanente para poder consultar despues que se hablo, no
memoria activa que E.V use en el contexto (eso lo hace memory_tools.py).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_BASE_DIR, "data", "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "ivy.log")
_CONV_LOG_PATH = os.path.join(_LOG_DIR, "conversaciones.log")

os.makedirs(_LOG_DIR, exist_ok=True)

logger = logging.getLogger("ivy")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    _file_handler = RotatingFileHandler(_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)

    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    logger.addHandler(_console_handler)

_conv_logger = logging.getLogger("ivy.conversaciones")
_conv_logger.setLevel(logging.INFO)
_conv_logger.propagate = False

if not _conv_logger.handlers:
    _conv_handler = logging.FileHandler(_CONV_LOG_PATH, encoding="utf-8")
    _conv_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _conv_logger.addHandler(_conv_handler)


def log_tool_call(name: str, tool_input: dict, result: str) -> None:
    logger.info("HERRAMIENTA %s(%s) -> %s", name, tool_input, str(result)[:300])


def log_confirmation(command: str, respuesta_usuario: str, aprobado: bool) -> None:
    veredicto = "APROBADO" if aprobado else "CANCELADO"
    logger.info("CONFIRMACION [%s] comando=%r respuesta_usuario=%r", veredicto, command, respuesta_usuario)


def log_error(mensaje: str) -> None:
    logger.error(mensaje)


def log_conversation(speaker_name: str, is_owner: bool, user_text: str, ivy_text: str) -> None:
    quien = speaker_name or "desconocido"
    if speaker_name and is_owner:
        quien += " (propietario)"
    _conv_logger.info("TU [%s]: %s", quien, user_text)
    _conv_logger.info("E.V: %s", ivy_text)
