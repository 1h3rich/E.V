"""
Modulo: speaker_id.py
------------------------
Identifica quien esta hablando comparando la voz de cada turno contra los
perfiles enrolados en data/voices/*.npy (ver enroll_voice.py en la raiz del
proyecto -- ese script NO es una herramienta de E.V, es de uso manual).

Carga perezosa: el modelo de resemblyzer (torch de por medio) solo se carga
la primera vez que hace falta, no al importar el modulo, para no penalizar
el arranque de E.V cuando no se ha enrolado ninguna voz todavia.
"""

import glob
import os

import numpy as np

from src import config

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES_DIR = os.path.join(_BASE_DIR, "data", "voices")

OWNER_PROFILE = "owner"

_encoder = None
_profiles = None  # dict nombre -> embedding numpy


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder()
    return _encoder


def _load_profiles() -> dict:
    global _profiles
    if _profiles is not None:
        return _profiles

    _profiles = {}
    if os.path.isdir(VOICES_DIR):
        for path in glob.glob(os.path.join(VOICES_DIR, "*.npy")):
            nombre = os.path.splitext(os.path.basename(path))[0]
            _profiles[nombre] = np.load(path)
    return _profiles


def warm_up() -> None:
    """Carga el codificador (torch) por adelantado si hay perfiles enrolados."""
    if has_enrolled_profiles():
        _get_encoder().embed_utterance(np.zeros(16000, dtype=np.float32))


def reload_profiles() -> None:
    """Fuerza a releer data/voices/*.npy (usar tras enrolar una voz nueva)."""
    global _profiles
    _profiles = None
    _load_profiles()


def has_enrolled_profiles() -> bool:
    return bool(_load_profiles())


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def identify(audio_path: str):
    """
    Identifica quien habla en audio_path.
    Devuelve (nombre_o_None, es_propietario, score).
    Si no hay ningun perfil enrolado, devuelve (None, False, 0.0) sin fallar
    -- E.V sigue funcionando igual que sin reconocimiento de voz.
    """
    profiles = _load_profiles()
    if not profiles:
        return None, False, 0.0

    from resemblyzer import preprocess_wav

    try:
        wav = preprocess_wav(audio_path)
        embed = _get_encoder().embed_utterance(wav)
    except Exception:
        return None, False, 0.0

    mejor_nombre = None
    mejor_score = 0.0
    for nombre, perfil in profiles.items():
        score = _cosine_similarity(embed, perfil)
        if score > mejor_score:
            mejor_score = score
            mejor_nombre = nombre

    if mejor_score < config.VOICE_MATCH_THRESHOLD:
        return None, False, mejor_score

    return mejor_nombre, (mejor_nombre == OWNER_PROFILE), mejor_score
