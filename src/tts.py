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
import queue
import re
import shutil
import subprocess
import threading
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

    # PCM crudo por stdin a pw-play (PipeWire), segun Piper va sintetizando:
    # sin wav temporal y sin el arranque de mpv (~0.2 s menos por frase).
    # Idea tomada del fork Aria. Si no hay pw-play, se usa mpv como antes.
    proceso = None
    try:
        for chunk in _get_voice().synthesize(text, syn_config=_syn_config):
            if proceso is None:
                proceso = subprocess.Popen(
                    _player_cmd(chunk.sample_rate, chunk.sample_channels),
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _current_process = proceso
            try:
                proceso.stdin.write(chunk.audio_int16_bytes)
            except BrokenPipeError:
                break  # stop() corto la reproduccion
        if proceso is not None:
            try:
                proceso.stdin.close()
            except OSError:
                pass
            proceso.wait()
    finally:
        _current_process = None


_PW_PLAY = shutil.which("pw-play")


def _player_cmd(rate: int, channels: int) -> list:
    r, c = str(rate), str(channels)
    if _PW_PLAY:
        return [_PW_PLAY, "--raw", "--rate", r, "--channels", c, "--format", "s16", "-"]
    return ["mpv", "--no-video", "--really-quiet", "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={r}", f"--demuxer-rawaudio-channels={c}",
            "--demuxer-rawaudio-format=s16le", "-"]


def warm_up() -> None:
    """Carga el modelo y hace una sintesis corta para calentar onnxruntime."""
    voice = _get_voice()
    with wave.open(os.devnull, "wb") as wav_file:
        voice.synthesize_wav("Hola.", wav_file, syn_config=_syn_config)


class SpeechStream:
    """
    Habla frases segun van llegando (p.ej. mientras el LLM sigue generando).
    Un hilo consume la cola: sintetiza y reproduce cada frase en orden, asi la
    primera frase suena sin esperar a que termine la respuesta completa.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._cancelled = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        if not self._cancelled and text and text.strip():
            self._queue.put(text)

    def finish(self) -> None:
        """Espera a que se diga todo lo encolado y cierra el stream."""
        global _active_stream
        self._queue.put(None)
        self._thread.join()
        if _active_stream is self:
            _active_stream = None

    def wait_idle(self) -> None:
        """Espera a que la cola actual se vacie (sin cerrar el stream)."""
        self._queue.join()

    def cancel(self) -> None:
        self._cancelled = True
        try:
            while True:
                self._queue.get_nowait()
                self._queue.task_done()
        except queue.Empty:
            pass

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if not self._cancelled:
                    speak(item)
            finally:
                self._queue.task_done()


_active_stream = None


def start_stream() -> SpeechStream:
    global _active_stream
    _active_stream = SpeechStream()
    return _active_stream


def active_stream():
    return _active_stream


def stop():
    """Cancela la reproduccion (y las frases pendientes) si hay algo sonando."""
    if _active_stream:
        _active_stream.cancel()
    if _current_process and _current_process.poll() is None:
        _current_process.terminate()
