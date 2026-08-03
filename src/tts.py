"""
Modulo: tts.py  (Text-To-Speech = Texto a Voz)
-----------------------------------------------
Convierte texto en audio y lo reproduce por los altavoces.

Sintesis local con Piper (onnx), sin depender de la nube ni de conexion a
internet -- antes usabamos edge-tts (voces de Microsoft Edge por API).
Los pesos del modelo de voz viven en data/piper/. Para reproducir el wav
generado usamos mpv, que ya viene instalado en CachyOS.

Voz usada: es_ES-sharvard-medium, hablante femenina (speaker_id=1) -- modelo
multi-hablante espanol de Espana, local. El mismo .onnx incluye tambien una
voz masculina (speaker_id=0) por si se quiere volver a cambiar.
Para usar otra voz/modelo: descargar el .onnx + .onnx.json de
https://huggingface.co/rhasspy/piper-voices en data/piper/ y actualizar
_MODEL_PATH (y _SPEAKER_ID si el modelo es multi-hablante).
"""

import os
import re
import subprocess
import tempfile
import wave

from piper import PiperVoice
from piper.config import SynthesisConfig


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_PATH = os.path.join(_BASE_DIR, "data", "piper", "es_ES-sharvard-medium.onnx")
_SPEAKER_ID = 1  # 0 = voz masculina, 1 = voz femenina (ver speaker_id_map del modelo)

# Modelo cargado perezosamente la primera vez que se necesita (tarda un
# poco, igual que la carga de Whisper en stt.py).
_voice = None
_syn_config = SynthesisConfig(speaker_id=_SPEAKER_ID)

# Proceso de reproduccion actual (guardado para poder cancelarlo)
_current_process = None


def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        _voice = PiperVoice.load(_MODEL_PATH)
    return _voice


def _strip_markdown(text: str) -> str:
    """
    Red de seguridad: el prompt le pide al modelo que no use markdown porque
    la respuesta se lee en voz alta, pero no siempre lo respeta (sobre todo
    en resumenes largos con listas y negritas). Si se le pasa tal cual a
    Piper, lee simbolos como '#', '*' o '-' literalmente y suena a robot
    leyendo codigo en vez de hablando. Esto limpia el texto justo antes de
    sintetizarlo, sin tocar lo que se guarda en el log de conversacion.
    """
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)   # bloques de codigo
    text = re.sub(r'`([^`]*)`', r'\1', text)                  # `codigo` inline
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)               # **negrita**
    text = re.sub(r'__(.*?)__', r'\1', text)                   # __negrita__
    text = re.sub(r'(?<!\w)\*(?!\s)(.*?)(?<!\s)\*(?!\w)', r'\1', text)  # *cursiva*
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)  # # encabezados
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)       # - listas
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)     # 1. listas
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)       # [texto](enlace)
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', '. ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def speak(text: str):
    """
    Convierte el texto a voz y lo reproduce por los altavoces.
    Bloquea hasta que termina de hablar.
    """
    global _current_process

    text = _strip_markdown(text)
    if not text:
        return

    temp_path = tempfile.mktemp(suffix='.wav', dir='/tmp')

    try:
        with wave.open(temp_path, "wb") as wav_file:
            _get_voice().synthesize_wav(text, wav_file, syn_config=_syn_config)

        # mpv reproduce el WAV en modo silencioso (sin abrir ventana ni mostrar barra)
        _current_process = subprocess.Popen(
            ["mpv", "--no-video", "--really-quiet", temp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        _current_process.wait()  # Esperamos a que termine

    finally:
        _current_process = None
        if os.path.exists(temp_path):
            os.remove(temp_path)


def stop():
    """Cancela la reproduccion si esta sonando algo."""
    global _current_process
    if _current_process and _current_process.poll() is None:
        _current_process.terminate()
