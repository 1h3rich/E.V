"""
Modulo: media_tools.py
------------------------
Control de reproduccion multimedia (playerctl, via MPRIS) y volumen
(wpctl, Pipewire).
"""

import subprocess

TOOLS = [
    {
        "name": "media_control",
        "description": "Controla la reproduccion multimedia actual (musica, video, podcast...).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play_pause", "next", "previous", "stop"],
                    "description": "Accion a realizar"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "set_volume",
        "description": "Fija el volumen del sistema a un porcentaje concreto (0-100).",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Porcentaje de volumen, 0 a 100"}
            },
            "required": ["level"]
        }
    },
    {
        "name": "volume_step",
        "description": "Sube o baja el volumen del sistema un paso pequeno (5%).",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "Direccion"}
            },
            "required": ["direction"]
        }
    },
]

_PLAYERCTL_ACTIONS = {
    "play_pause": "play-pause",
    "next": "next",
    "previous": "previous",
    "stop": "stop",
}


def media_control(action: str) -> str:
    playerctl_action = _PLAYERCTL_ACTIONS.get(action)
    if not playerctl_action:
        return f"Accion de medios desconocida: {action}"

    result = subprocess.run(
        ["playerctl", playerctl_action],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        return f"No hay ningun reproductor activo ({result.stderr.strip()})."
    return "Hecho."


def set_volume(level: int) -> str:
    level = max(0, min(100, int(level)))
    subprocess.run(
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level}%"],
        capture_output=True, timeout=5
    )
    return f"Volumen al {level} por ciento."


def volume_step(direction: str) -> str:
    sign = "+" if direction == "up" else "-"
    subprocess.run(
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"5%{sign}"],
        capture_output=True, timeout=5
    )
    return "Volumen ajustado."


def execute(tool_name: str, tool_input: dict) -> str:
    if tool_name == "media_control":
        return media_control(tool_input["action"])
    elif tool_name == "set_volume":
        return set_volume(tool_input["level"])
    elif tool_name == "volume_step":
        return volume_step(tool_input["direction"])
    else:
        return f"Herramienta desconocida: {tool_name}"
