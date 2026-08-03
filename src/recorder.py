"""
Módulo: recorder.py
-------------------
Graba audio desde el micrófono del ordenador.

Cómo funciona:
- start() abre el micrófono y empieza a capturar muestras de audio.
- Cada "trozo" de audio que llega del micrófono se guarda en una lista.
- stop() junta todos los trozos, los guarda como archivo WAV temporal y devuelve la ruta.

WAV es un formato de audio sin compresión. Whisper lo necesita para transcribir.
"""

import tempfile
import os

import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wav


# Frecuencia de muestreo: 16000 muestras por segundo
# Whisper fue entrenado con audio a 16kHz, así que esto es ideal
SAMPLE_RATE = 16000


class AudioRecorder:

    def __init__(self):
        self._recording = False
        self._frames = []   # Lista donde acumulamos los trozos de audio
        self._stream = None # El objeto que controla el micrófono

    def start(self):
        """Abre el micrófono y empieza a grabar."""
        self._frames = []
        self._recording = True

        # InputStream es un flujo de audio entrante desde el micrófono.
        # callback se llama automáticamente cada vez que llegan nuevas muestras.
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,         # Mono (un canal)
            dtype='int16',      # 16 bits por muestra (calidad estándar de voz)
            callback=self._callback
        )
        self._stream.start()

    def stop(self):
        """
        Detiene la grabación y guarda el audio en un archivo WAV temporal.
        Devuelve la ruta al archivo, o None si no se grabó nada.
        """
        self._recording = False

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None

        # Unimos todos los trozos en un solo array de numpy
        audio_data = np.concatenate(self._frames, axis=0)

        # Creamos un archivo temporal con extensión .wav
        temp_path = tempfile.mktemp(suffix='.wav', dir='/tmp')
        wav.write(temp_path, SAMPLE_RATE, audio_data)

        return temp_path

    def _callback(self, indata, frames, time, status):
        """
        Esta función la llama sounddevice automáticamente con cada trozo de audio.
        Solo guardamos si estamos grabando activamente.
        """
        if self._recording:
            self._frames.append(indata.copy())
