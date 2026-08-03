"""
Modulo: system_tools.py
------------------------
Herramientas de control del sistema: abrir aplicaciones y terminales,
escribir texto simulando teclado, y ejecutar comandos de shell.

Seguridad:
- sudo esta bloqueado siempre en run_shell_command, sin excepcion.
- Los comandos que coincidan con DANGER_PATTERNS no se ejecutan aqui: se
  lanza NeedsConfirmation para que ai_client.py pida confirmacion por voz
  antes de ejecutarlos de verdad (ver _call_claude en ai_client.py).
"""

import glob
import os
import re
import shutil
import subprocess

HOME = os.path.expanduser("~")

DESKTOP_DIRS = [
    "/usr/share/applications",
    os.path.join(HOME, ".local/share/applications"),
]

# Patrones (case-insensitive) que exigen confirmacion hablada antes de ejecutar.
DANGER_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f",       # rm -rf y variantes (-fr, -Rf, etc.)
    r"\bmkfs",
    r"\bdd\s+if=",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb clasica
    r">\s*/dev/sd[a-z]",
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\bsystemctl\s+.*\b(stop|disable|mask)\b",
    r"\bpasswd\b",
    r"\buseradd\b|\buserdel\b",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bgit\s+push\s+.*--force",
]
_DANGER_RE = re.compile("|".join(DANGER_PATTERNS), re.IGNORECASE)


class NeedsConfirmation(Exception):
    """Se lanza cuando un comando de shell coincide con un patron peligroso."""

    def __init__(self, command: str):
        self.command = command
        super().__init__(command)


TOOLS = [
    {
        "name": "open_app",
        "description": (
            "Abre una aplicacion instalada en el ordenador por su nombre. "
            "Usa esto cuando el usuario pida abrir un programa (navegador, editor, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nombre de la aplicacion. Ejemplos: 'Firefox', 'Discord', 'Dolphin'"
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "open_terminal",
        "description": "Abre una nueva ventana de terminal (konsole).",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "type_text",
        "description": (
            "Escribe texto automaticamente donde este el foco/cursor actual, "
            "simulando teclado. Usa esto cuando el usuario diga 'escribe...' "
            "sobre una terminal o cualquier ventana enfocada."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto exacto a escribir"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "run_shell_command",
        "description": (
            "Ejecuta un comando de shell en el sistema del usuario y devuelve su salida. "
            "Usa esto para tareas de sistema, automatizacion, git, gestion de ficheros, etc. "
            "Nunca incluyas 'sudo': si el usuario pide algo que requiere privilegios de "
            "administrador, dile que lo ejecute el manualmente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando de shell a ejecutar"}
            },
            "required": ["command"]
        }
    },
]


def _find_desktop_file(name: str):
    pattern_name = name.strip().lower().replace(" ", "")
    for base in DESKTOP_DIRS:
        for path in glob.glob(os.path.join(base, "*.desktop")):
            filename = os.path.basename(path).lower()
            if pattern_name in filename.replace(" ", ""):
                return path
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.lower().startswith("name=") and name.strip().lower() in line.lower():
                            return path
            except OSError:
                continue
    return None


def open_app(name: str) -> str:
    desktop_file = _find_desktop_file(name)
    if desktop_file:
        subprocess.Popen(
            ["gio", "launch", desktop_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return f"Abriendo {name}."

    binary = shutil.which(name.strip().lower())
    if binary:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return f"Abriendo {name}."

    return f"No he encontrado ninguna aplicacion llamada '{name}'."


def open_terminal() -> str:
    subprocess.Popen(["konsole"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return "Terminal abierta."


def type_text(text: str) -> str:
    if not shutil.which("ydotool"):
        return (
            "No puedo escribir texto: falta ydotool. Pide al usuario que instale "
            "ydotool y ponga en marcha ydotoold (pasos manuales pendientes)."
        )
    try:
        result = subprocess.run(
            ["ydotool", "type", "--", text],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return f"Fallo al escribir con ydotool: {result.stderr.strip()}"
        return "Texto escrito."
    except subprocess.TimeoutExpired:
        return "ydotool tardo demasiado y se cancelo."
    except Exception as e:
        return f"Error al escribir texto: {e}"


def run_shell_command(command: str) -> str:
    if re.search(r"\bsudo\b", command, re.IGNORECASE):
        return (
            "No puedo ejecutar comandos con sudo. Dile al usuario que lo ejecute "
            "el mismo manualmente."
        )

    if _DANGER_RE.search(command):
        raise NeedsConfirmation(command)

    return _execute_shell(command)


def _execute_shell(command: str) -> str:
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=30
        )
        salida = (result.stdout or "").strip()
        error = (result.stderr or "").strip()

        partes = [f"Codigo de salida: {result.returncode}"]
        if salida:
            partes.append(f"Salida:\n{salida[:2000]}")
        if error:
            partes.append(f"Error:\n{error[:2000]}")
        return "\n".join(partes)
    except subprocess.TimeoutExpired:
        return "El comando tardo demasiado (mas de 30s) y se cancelo."
    except Exception as e:
        return f"Error al ejecutar el comando: {e}"


def execute(tool_name: str, tool_input: dict) -> str:
    if tool_name == "open_app":
        return open_app(tool_input["name"])
    elif tool_name == "open_terminal":
        return open_terminal()
    elif tool_name == "type_text":
        return type_text(tool_input["text"])
    elif tool_name == "run_shell_command":
        return run_shell_command(tool_input["command"])
    else:
        return f"Herramienta desconocida: {tool_name}"
