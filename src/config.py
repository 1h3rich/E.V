import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
ANTHROPIC_KEY = os.getenv("ANTHROPIC_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_KEY", "")

# Proveedor de IA: "ollama" (local, gratis), "claude" u "openai"
AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama")

# LLM local via Ollama (ver ai_client._call_ollama). Si Ollama no responde y hay
# ANTHROPIC_KEY, se cae automaticamente a Claude para no dejar a Ivy muda.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# Modelo de Whisper local para transcribir tu voz
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# Idioma para la transcripción
LANGUAGE = os.getenv("LANGUAGE", "es")

# Similitud coseno minima (0-1) para reconocer una voz enrolada (ver src/speaker_id.py).
# Bajado de 0.75 a 0.65 el 2026-08-01 tras calibrar con voz real: el propietario obtenia
# score=0.760 en una condicion normal, un margen demasiado justo sobre 0.75 -- variaciones
# normales de tono/distancia al microfono caian por debajo y lo dejaban fuera todo el rato.
VOICE_MATCH_THRESHOLD = float(os.getenv("VOICE_MATCH_THRESHOLD", "0.65"))

# Atajo de teclado para activar/desactivar la escucha continua (ver src/hotkey.py).
# HOTKEY_MODIFIER admite combinarse con "+": "super", "ctrl", "alt", "shift",
# "ctrl+alt", "ctrl+shift", "alt+shift", "super+shift", etc.
HOTKEY_MODIFIER = os.getenv("HOTKEY_MODIFIER", "super")
HOTKEY_KEY = os.getenv("HOTKEY_KEY", "y")
