"""
Modulo: system_tools.py
------------------------
Herramientas de control del sistema: abrir aplicaciones y terminales,
escribir texto simulando teclado, y ejecutar comandos de shell.

Seguridad (modelo tomado del fork Aria, ampliado):
- NUNCA se usa shell. El comando se parte con shlex y se ejecuta como lista de
  argumentos (shell=False): no hay tuberias, redirecciones, encadenado ni
  $(...). Antes era `bash -c` + lista negra de regex, y `s\\udo`, `"su""do"`,
  `rm -r -f ~` o `cat /dev/zero > /dev/nvme0n1` se colaban.
- LISTA BLANCA: si el binario no esta listado, no se ejecuta. sudo, su, pkexec
  y los interpretes (bash, python, perl...) quedan fuera por omision.
- Los comandos que modifican algo lanzan NeedsConfirmation y ai_client.py pide
  confirmacion por voz antes de ejecutarlos. type_text tambien: con una
  terminal enfocada equivale a ejecutar cualquier cosa.
- Se vetan argumentos que convierten un binario permitido en ejecucion o
  escritura arbitraria (find -exec, fd -x, git -c, sort -o...).
"""

import glob
import os
import shlex
import shutil
import subprocess

HOME = os.path.expanduser("~")

DESKTOP_DIRS = [
    "/usr/share/applications",
    os.path.join(HOME, ".local/share/applications"),
]

# Solo tienen sentido con un shell de por medio: indican encadenar, redirigir o expandir.
# (~ se expande a mano; * y ? no son peligrosos sin shell: llegan literales.)
METACARACTERES = set(';|&$`><\n\r\\!(){}')

# Se ejecutan directamente.
SOLO_LECTURA = {
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "fd", "file", "stat",
    "du", "df", "free", "uptime", "uname", "whoami", "id", "date", "cal",
    "which", "pwd", "tree", "sort", "cut", "nl", "ps", "pgrep", "lsblk",
    "lscpu", "lsusb", "lspci", "journalctl", "nproc", "basename", "dirname",
    "realpath", "readlink", "find", "echo", "sensors", "ip", "ss",
}

# Exigen confirmacion.
MODIFICAN = {
    "rm", "mv", "cp", "mkdir", "rmdir", "touch", "ln", "chmod", "chown",
    "kill", "pkill", "tar", "unzip", "zip", "gzip", "gunzip",
}

# Se decide por el subcomando: los de "lectura" van directos, el resto se confirma.
SUBCOMANDOS = {
    "git": {"status", "log", "diff", "show", "branch", "remote", "tag", "describe",
            "blame", "shortlog", "ls-files", "rev-parse"},
    "systemctl": {"status", "list-units", "list-timers", "is-active", "is-enabled",
                  "show", "cat", "list-unit-files"},
    "docker": {"ps", "images", "logs", "inspect", "version", "info", "stats"},
    "flatpak": {"list", "info", "remotes", "history"},
    "pacman": {"-Q", "-Qi", "-Ql", "-Qs", "-Qe", "-Qm", "-Qu", "-Si", "-Ss"},
}

# git branch/tag/remote listan... o crean/borran segun los argumentos.
_GIT_LISTADO = {"branch", "tag", "remote"}
_GIT_LISTADO_OK = {"-a", "-r", "-v", "-vv", "-l", "--list", "--all", "--remotes", "--verbose"}

# Argumentos que convierten un comando permitido en ejecucion o escritura arbitraria.
# Las entradas de una letra ("-x") se buscan tambien dentro de grupos como "-Hx".
ARGS_PROHIBIDOS = {
    "find": {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
             "-fprintf", "-fls"},
    "fd": {"-x", "-X", "--exec", "--exec-batch"},
    "git": {"-c", "--exec-path", "--upload-pack", "--receive-pack", "--output", "--ext-diff"},
    "rg": {"--pre", "--hostname-bin"},
    "sort": {"-o", "--output", "--compress-program"},
    "tree": {"-o"},
    "file": {"-C", "--compile"},
    "tar": {"-I", "--to-command", "--use-compress-program", "--checkpoint-action",
            "--info-script", "--new-volume-script"},
    "journalctl": {"--vacuum-size", "--vacuum-time", "--vacuum-files", "--rotate",
                   "--flush", "--relinquish-var", "--setup-keys", "--update-catalog"},
}
# ip/ss solo para consultar: estas palabras cambian la red.
_IP_MODIFICA = {"add", "del", "delete", "set", "flush", "change", "replace", "append", "up", "down"}
# Agrupables en flags cortos (-abc).
_AGRUPABLES = {"fd", "sort", "tree", "tar", "file"}


class NeedsConfirmation(Exception):
    """
    La accion es legitima pero modifica algo: ai_client pide confirmacion por
    voz y, si se aprueba, llama a run() para ejecutarla de verdad.
    """

    def __init__(self, description: str, run):
        self.command = description  # lo que se le lee al usuario
        self.run = run
        super().__init__(description)


class ComandoRechazado(Exception):
    """El comando no pasa la lista blanca o usa argumentos prohibidos."""


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
            "Ejecuta UN comando acotado del sistema y devuelve su salida. "
            "Solo se admiten comandos de una lista blanca (ls, cat, grep, find, df, free, "
            "ps, git status/log/diff, systemctl status, journalctl, rm, mv, cp, mkdir...) "
            "y SIN shell: no funcionan tuberias (|), redirecciones (>), encadenado (; &&) "
            "ni sustitucion $(...). Para calculos, calcula tu mismo. "
            "Los que modifican algo (rm, mv, cp, git commit...) requieren que el usuario "
            "lo confirme. sudo no esta disponible: si algo necesita privilegios, dile al "
            "usuario que lo ejecute el mismo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "Un solo comando, sin tuberias ni redirecciones"}
            },
            "required": ["command"]
        }
    },
]


def _arg_prohibido(binario: str, arg: str, prohibidos: set) -> bool:
    if arg in prohibidos or any(arg.startswith(p + "=") for p in prohibidos if p.startswith("--")):
        return True
    # "-o" pegado a su valor ("-ofichero") o dentro de un grupo ("-uo", "-Hx").
    if binario in _AGRUPABLES and arg.startswith("-") and not arg.startswith("--"):
        cortos = {p[1] for p in prohibidos if len(p) == 2}
        return any(letra in cortos for letra in arg[1:])
    return False


def _clasificar(command: str):
    """
    Valida el comando y devuelve (argv, necesita_confirmacion).
    Lanza ComandoRechazado si no procede ejecutarlo.
    """
    if not command or not command.strip():
        raise ComandoRechazado("Comando vacio.")

    hallados = sorted(set(c for c in command if c in METACARACTERES))
    if hallados:
        raise ComandoRechazado(
            f"Ese comando usa {' '.join(hallados)}, y aqui los comandos se ejecutan sin "
            "shell: no hay tuberias, redirecciones, variables ni encadenado. Usa un comando simple."
        )

    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise ComandoRechazado(f"No he podido interpretar el comando: {e}")
    if not argv:
        raise ComandoRechazado("Comando vacio.")

    argv = [os.path.expanduser(a) if a.startswith("~") else a for a in argv]
    binario = argv[0]
    if os.path.basename(binario) != binario:
        raise ComandoRechazado("Indica solo el nombre del programa, sin ruta.")

    prohibidos = ARGS_PROHIBIDOS.get(binario, set())
    for arg in argv[1:]:
        if _arg_prohibido(binario, arg, prohibidos):
            raise ComandoRechazado(
                f"El argumento '{arg}' no esta permitido con {binario}: "
                "permitiria ejecutar o escribir cosas fuera de control."
            )

    if binario in SUBCOMANDOS:
        if binario == "pacman":
            sub = argv[1] if len(argv) > 1 else ""
        else:
            sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        lectura = sub in SUBCOMANDOS[binario]
        if binario == "git" and sub in _GIT_LISTADO:
            resto = argv[argv.index(sub) + 1:]
            lectura = all(a in _GIT_LISTADO_OK for a in resto)
        return argv, not lectura

    if binario in ("ip", "ss"):
        return argv, any(a in _IP_MODIFICA for a in argv[1:])

    if binario in SOLO_LECTURA:
        return argv, False

    if binario in MODIFICAN:
        return argv, True

    raise ComandoRechazado(
        f"'{binario}' no esta en la lista de comandos permitidos. "
        "Si de verdad hace falta, el usuario puede ejecutarlo el mismo en una terminal."
    )


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
    # Con una terminal enfocada, teclear es ejecutar cualquier cosa (sudo
    # incluido) saltandose la lista blanca: siempre se confirma.
    raise NeedsConfirmation(f"escribir en la ventana activa: {text[:200]}",
                            lambda: _type_text_now(text))


def _type_text_now(text: str) -> str:
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
    try:
        argv, necesita_confirmacion = _clasificar(command)
    except ComandoRechazado as e:
        return str(e)

    if necesita_confirmacion:
        raise NeedsConfirmation(command, lambda: _execute(argv))

    return _execute(argv)


def _execute(argv: list) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True, text=True, timeout=30,
            cwd=HOME, shell=False,
        )
        salida = (result.stdout or "").strip()
        error = (result.stderr or "").strip()

        partes = [f"Codigo de salida: {result.returncode}"]
        if salida:
            partes.append(f"Salida:\n{salida[:2000]}")
        if error:
            partes.append(f"Error:\n{error[:2000]}")
        return "\n".join(partes)
    except FileNotFoundError:
        return f"No existe el programa '{argv[0]}' en este sistema."
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
