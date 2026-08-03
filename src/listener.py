"""
Modulo: listener.py
--------------------
Escucha continua del microfono mientras E.V esta activa.

En vez de "pulsa para grabar, pulsa para parar", aqui el microfono se queda
abierto todo el tiempo que E.V este en estado ESCUCHANDO. Segmentamos la
voz en "turnos": detectamos cuando el usuario empieza a hablar y cuando se
calla el tiempo suficiente como para considerar que ha terminado la frase.

Deteccion de voz/silencio: cada bloque de audio que llega del microfono se
mide con RMS (energia media). Si supera un umbral, es voz; si no, silencio.
Tras un tramo de voz, si hay suficiente silencio seguido (HANGOVER_SEC), se
cierra el turno y se entrega para transcribir.

Mientras E.V esta hablando (TTS reproduciendose) hay que llamar a mute() /
unmute() desde fuera para que no se transcriba a si misma.
"""

import tempfile
import threading
import time

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd

SAMPLE_RATE = 16000
BLOCK_DURATION = 0.03  # 30ms por bloque
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION)

SILENCE_RMS_THRESHOLD = 500   # energia RMS (int16) por debajo de esto = silencio
HANGOVER_SEC = 0.8            # silencio tras voz necesario para cerrar el turno
MIN_SPEECH_SEC = 0.3          # turnos mas cortos que esto se descartan (ruido)
MUTE_GRACE_SEC = 0.3          # gracia extra tras unmute() para evitar cola de eco

# Barge-in (cortar a E.V si el usuario habla mientras ella esta hablando).
# Umbral mas alto que SILENCE_RMS_THRESHOLD y con tiempo sostenido, porque sin
# cancelacion de eco el propio audio de E.V sale por los altavoces y puede
# colarse en el microfono -- sin este margen, E.V se cortaria a si misma nada
# mas empezar a hablar. Con auriculares no hay ese problema y podria bajarse.
BARGE_IN_RMS_THRESHOLD = 3200
BARGE_IN_MIN_SEC = 0.45


class ContinuousListener:
    """
    Escucha el microfono en continuo y entrega turnos de habla completos
    llamando a `on_utterance(audio_path)` (en un hilo aparte) segun se van
    cerrando.
    """

    def __init__(self, on_utterance):
        self._on_utterance = on_utterance
        self._stream = None
        self._active = False

        self._speech_frames = []
        self._in_speech = False
        self._silence_run = 0.0

        self._muted = False
        self._muted_until = 0.0
        self._on_barge_in = None
        self._barge_in_run = 0.0

    def start(self):
        """Abre el microfono y empieza a escuchar en continuo."""
        self._active = True
        self._muted = False
        self._muted_until = 0.0
        self._reset_turn()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='int16',
            blocksize=BLOCK_SIZE,
            callback=self._callback
        )
        self._stream.start()

    def stop(self):
        """Cierra el microfono y descarta cualquier turno a medias."""
        self._active = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._reset_turn()

    def mute(self, on_barge_in=None):
        """
        Ignora el microfono para no transcribirse a si misma (usar justo antes
        de que E.V empiece a hablar). Si se pasa on_barge_in, se llama (una
        sola vez, en un hilo aparte) en cuanto se detecta voz fuerte y
        sostenida mientras esta muteado -- pensado para que main.py corte el
        TTS con tts.stop() si el usuario empieza a hablar por encima de E.V.
        """
        self._muted = True
        self._on_barge_in = on_barge_in
        self._barge_in_run = 0.0
        self._reset_turn()

    def unmute(self, grace_seconds: float = MUTE_GRACE_SEC):
        """Vuelve a escuchar (usar justo despues de que E.V termine de hablar)."""
        self._muted = False
        self._on_barge_in = None
        self._muted_until = time.time() + grace_seconds

    def _reset_turn(self):
        self._speech_frames = []
        self._in_speech = False
        self._silence_run = 0.0

    def _callback(self, indata, frames, time_info, status):
        if not self._active:
            return

        if self._muted or time.time() < self._muted_until:
            if self._muted and self._on_barge_in:
                self._check_barge_in(indata)
            self._reset_turn()
            return

        block = indata.copy()
        rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))

        if rms >= SILENCE_RMS_THRESHOLD:
            self._in_speech = True
            self._silence_run = 0.0
            self._speech_frames.append(block)
        elif self._in_speech:
            self._silence_run += BLOCK_DURATION
            self._speech_frames.append(block)  # guardamos la cola de silencio, transcribe mejor

            if self._silence_run >= HANGOVER_SEC:
                self._close_turn()

    def _check_barge_in(self, indata):
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))

        if rms >= BARGE_IN_RMS_THRESHOLD:
            self._barge_in_run += BLOCK_DURATION
            if self._barge_in_run >= BARGE_IN_MIN_SEC:
                handler = self._on_barge_in
                self._on_barge_in = None  # una sola vez por cada mute()
                threading.Thread(target=handler, daemon=True).start()
        else:
            self._barge_in_run = 0.0

    def _close_turn(self):
        frames = self._speech_frames
        self._reset_turn()

        total_sec = len(frames) * BLOCK_DURATION
        if total_sec < MIN_SPEECH_SEC:
            return  # ruido corto, no era una frase real

        audio_data = np.concatenate(frames, axis=0)
        temp_path = tempfile.mktemp(suffix='.wav', dir='/tmp')
        wav.write(temp_path, SAMPLE_RATE, audio_data)

        # Hilo aparte: si procesaramos aqui dentro bloqueariamos el callback
        # de audio y sounddevice empezaria a perder bloques.
        threading.Thread(target=self._on_utterance, args=(temp_path,), daemon=True).start()
