"""
Modulo: file_tools.py
---------------------
Herramientas de acceso al sistema de archivos para el asistente.

Cuando el usuario pregunta "que hay en el Escritorio?" o "lee ese archivo",
Claude llama a estas funciones para obtener la respuesta real.

Seguridad: solo se permite acceder dentro del directorio personal del usuario.
"""

import fnmatch
import os

HOME = os.path.expanduser("~")

# Definicion de las herramientas en el formato que entiende la API de Claude
TOOLS = [
    {
        "name": "list_directory",
        "description": (
            "Lista los archivos y carpetas de un directorio del ordenador del usuario. "
            "Usa esto cuando el usuario pregunte que hay en una carpeta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta del directorio. Ejemplos: ~, ~/Escritorio, ~/Documentos"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_file",
        "description": (
            "Lee el contenido de un archivo de texto. "
            "Usa esto cuando el usuario pida leer o consultar un archivo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Ruta completa al archivo. Ejemplo: ~/Escritorio/notas.txt"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "find_files",
        "description": (
            "Busca archivos por nombre en el ordenador del usuario. "
            "Usa esto cuando el usuario pregunte donde esta un archivo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name_pattern": {
                    "type": "string",
                    "description": "Nombre o patron a buscar. Ejemplos: 'main.py', '*.pdf', 'config'"
                },
                "search_in": {
                    "type": "string",
                    "description": "Directorio donde buscar. Por defecto todo el directorio personal."
                }
            },
            "required": ["name_pattern"]
        }
    }
]


def _safe_path(path: str) -> str:
    """
    Expande ~ y verifica que la ruta este dentro del home del usuario.
    Impide acceso a directorios del sistema.
    """
    expanded = os.path.expanduser(path)
    real = os.path.realpath(expanded)
    if not real.startswith(HOME):
        raise PermissionError(f"Acceso denegado. Solo se permite acceder a {HOME}")
    return real


def list_directory(path: str) -> str:
    try:
        real_path = _safe_path(path)

        if not os.path.exists(real_path):
            return f"El directorio no existe: {path}"
        if not os.path.isdir(real_path):
            return f"Eso no es un directorio: {path}"

        entries = sorted(os.listdir(real_path))
        carpetas = [e + "/" for e in entries if os.path.isdir(os.path.join(real_path, e))]
        archivos = [e for e in entries if os.path.isfile(os.path.join(real_path, e))]

        resultado = f"Contenido de {path}:\n"
        if carpetas:
            resultado += "Carpetas: " + ", ".join(carpetas) + "\n"
        if archivos:
            resultado += "Archivos: " + ", ".join(archivos) + "\n"
        if not carpetas and not archivos:
            resultado += "(vacio)"

        return resultado

    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"Error al listar directorio: {e}"


def read_file(path: str) -> str:
    try:
        real_path = _safe_path(path)

        if not os.path.exists(real_path):
            return f"El archivo no existe: {path}"
        if not os.path.isfile(real_path):
            return f"Eso no es un archivo: {path}"

        size = os.path.getsize(real_path)
        if size > 50_000:
            return f"Archivo demasiado grande ({size} bytes). Limite 50KB."

        with open(real_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        return f"Contenido de {path}:\n\n{content}"

    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"Error al leer archivo: {e}"


def find_files(name_pattern: str, search_in: str = "~") -> str:
    try:
        base_path = _safe_path(search_in)
        matches = []

        SKIP_DIRS = {".git", "venv", "node_modules", "__pycache__", ".cache"}

        for root, dirs, files in os.walk(base_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for filename in files:
                patron = name_pattern.lower()
                if fnmatch.fnmatch(filename.lower(), patron) or patron in filename.lower():
                    ruta = os.path.join(root, filename).replace(HOME, "~")
                    matches.append(ruta)

                if len(matches) >= 20:
                    break

            if len(matches) >= 20:
                break

        if not matches:
            return f"No se encontraron archivos con '{name_pattern}'"

        return f"Encontrados {len(matches)} archivo(s):\n" + "\n".join(matches)

    except PermissionError as e:
        return str(e)
    except Exception as e:
        return f"Error al buscar: {e}"


def execute(tool_name: str, tool_input: dict) -> str:
    """Ejecuta la herramienta indicada por Claude."""
    if tool_name == "list_directory":
        return list_directory(tool_input["path"])
    elif tool_name == "read_file":
        return read_file(tool_input["path"])
    elif tool_name == "find_files":
        return find_files(tool_input["name_pattern"], tool_input.get("search_in", "~"))
    else:
        return f"Herramienta desconocida: {tool_name}"
