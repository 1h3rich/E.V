"""
Módulo: stt.py  (Speech-To-Text = Voz a Texto)
-----------------------------------------------
Convierte un archivo de audio WAV en texto usando Whisper.

Whisper es un modelo de IA de OpenAI que funciona LOCALMENTE en tu ordenador.
No necesita conexión a internet ni API key. Lo descarga la primera vez y lo cachea.

Modelos disponibles (de más rápido a más preciso):
  tiny   → descarga ~75MB,  muy rápido,  menos preciso
  base   → descarga ~145MB, buen equilibrio  ← recomendado para empezar
  small  → descarga ~480MB, más preciso,  más lento al cargar
  medium → descarga ~1.5GB, muy preciso,  necesita buena CPU/GPU
"""

from faster_whisper import WhisperModel
from src import config


# Variable global para el modelo. Se carga una sola vez y queda en memoria.
_model = None


def _get_model():
    """Carga el modelo de Whisper si aún no está cargado."""
    global _model
    if _model is None:
        print(f"  [Cargando Whisper '{config.WHISPER_MODEL}'... solo tarda la primera vez]")
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device="cpu",       # Usamos CPU. Si tienes GPU NVIDIA, cambia a "cuda"
            compute_type="int8" # int8 es más ligero que float32, igual de preciso para voz
        )
    return _model


def transcribe(audio_path: str) -> str:
    """
    Toma la ruta de un archivo WAV y devuelve el texto que dice.

    `segments` es una lista de fragmentos con timestamps.
    Los juntamos todos para obtener el texto completo.
    """
    model = _get_model()

    language = config.LANGUAGE if config.LANGUAGE else "es"

    segments, _info = model.transcribe(
        audio_path,
        language=language,
        beam_size=1,       # greedy: ~5x más rápido que beam_size=5, sin pérdida notable
        vad_filter=True,   # salta silencios, reduce trabajo en audios largos
    )

    # segments es un generador lazy — forzamos la evaluación aquí
    text = " ".join(segment.text.strip() for segment in segments)
    return text.strip()
