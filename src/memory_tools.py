"""
Modulo: memory_tools.py
-------------------------
Notas de hechos/preferencias que E.V recuerda entre conversaciones.

No es un historial de conversacion completo: son notas puntuales que el
usuario pide guardar explicitamente ("recuerda que...") o que se olviden
("olvida que..."). Se guardan en texto plano en data/memory.md, una linea
por nota, y ai_client.py las inyecta enteras en el system prompt de cada
turno (el fichero es pequeño, no hace falta busqueda).
"""

import datetime
import difflib
import os
import re

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_PATH = os.path.join(_BASE_DIR, "data", "memory.md")

TOOLS = [
    {
        "name": "remember",
        "description": (
            "Guarda un hecho o preferencia del usuario para recordarlo en futuras "
            "conversaciones. Usa esto cuando el usuario diga 'recuerda que...', "
            "'no olvides que...' o algo equivalente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "El hecho a recordar, en una frase clara"}
            },
            "required": ["fact"]
        }
    },
    {
        "name": "forget",
        "description": (
            "Olvida una nota guardada previamente que coincida con el texto dado. "
            "Usa esto cuando el usuario diga 'olvida que...' o pida borrar algo que recuerdas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto que debe contener la nota a olvidar"}
            },
            "required": ["text"]
        }
    },
]


def read_memory() -> str:
    if not os.path.exists(MEMORY_PATH):
        return ""
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


_FECHA_PREFIX_RE = re.compile(r"^-\s*\[\d{4}-\d{2}-\d{2}\]\s*")
_DUPLICATE_SIMILARITY_THRESHOLD = 0.75


def _sin_prefijo_fecha(linea: str) -> str:
    return _FECHA_PREFIX_RE.sub("", linea).strip()


def _ya_recordado(fact: str) -> bool:
    fact_norm = fact.strip().lower()
    for linea in read_memory().splitlines():
        existente = _sin_prefijo_fecha(linea).lower()
        if not existente:
            continue
        if difflib.SequenceMatcher(None, fact_norm, existente).ratio() >= _DUPLICATE_SIMILARITY_THRESHOLD:
            return True
    return False


def remember(fact: str) -> str:
    fact = fact.strip()
    if _ya_recordado(fact):
        return "Ya tenía guardada una nota parecida, no la repito."

    os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
    fecha = datetime.date.today().isoformat()
    with open(MEMORY_PATH, "a", encoding="utf-8") as f:
        f.write(f"- [{fecha}] {fact}\n")
    return "Recordado."


def forget(text: str) -> str:
    if not os.path.exists(MEMORY_PATH):
        return "No hay nada guardado todavia."

    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        lineas = f.readlines()

    texto_buscado = text.strip().lower()
    restantes = [linea for linea in lineas if texto_buscado not in linea.lower()]
    eliminadas = len(lineas) - len(restantes)

    if eliminadas == 0:
        return f"No encontre ninguna nota que mencione '{text}'."

    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        f.writelines(restantes)

    return f"Olvidado ({eliminadas} nota(s) eliminada(s))."


def execute(tool_name: str, tool_input: dict) -> str:
    if tool_name == "remember":
        return remember(tool_input["fact"])
    elif tool_name == "forget":
        return forget(tool_input["text"])
    else:
        return f"Herramienta desconocida: {tool_name}"
