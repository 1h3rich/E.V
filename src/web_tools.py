"""
Modulo: web_tools.py
---------------------
Busqueda web y apertura de paginas.

web_search NO abre el navegador: hace scraping del HTML de DuckDuckGo
(sin API key, gratis) y devuelve titulo + resumen + URL de los primeros
resultados como texto, para que la IA pueda leerlos/responder por voz sin
sacar al usuario de lo que este haciendo. Si el usuario quiere ver una
pagina de verdad, la IA usa open_url con la URL que le interese -- eso SI
abre navegador, siempre Brave en modo incognito (pedido explicito del
usuario: no quiere dejar rastro de navegacion).
"""

import html
import re
import subprocess
import urllib.parse

import requests

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Busca algo en Internet y devuelve titulo, resumen y URL de los primeros "
            "resultados como texto, SIN abrir el navegador. Usa esto para responder "
            "preguntas o buscar informacion. Si el usuario quiere ver la pagina en si, "
            "usa open_url despues con la URL que interese."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Que buscar"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "open_url",
        "description": (
            "Abre una URL concreta en Brave, siempre en modo incognito (el usuario no "
            "quiere dejar rastro de navegacion)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL completa a abrir"}
            },
            "required": ["url"]
        }
    },
]

_TITLE_RE = re.compile(r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'<a class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)


def _strip_tags(fragment: str) -> str:
    sin_tags = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(sin_tags).strip()


def _open_brave_incognito(url: str) -> None:
    subprocess.Popen(
        ["brave", "--incognito", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.raise_for_status()

    titulos = _TITLE_RE.findall(resp.text)
    resumenes = _SNIPPET_RE.findall(resp.text)

    resultados = []
    for i, (url, titulo) in enumerate(titulos[:max_results]):
        resumen = resumenes[i] if i < len(resumenes) else ""
        resultados.append({
            "titulo": _strip_tags(titulo),
            "url": url,
            "resumen": _strip_tags(resumen),
        })
    return resultados


def web_search(query: str) -> str:
    try:
        resultados = _ddg_search(query)
    except requests.RequestException as e:
        return f"Error buscando '{query}': {e}"

    if not resultados:
        return f"No he encontrado resultados para '{query}'."

    partes = [f"Resultados para '{query}':"]
    for r in resultados:
        partes.append(f"- {r['titulo']} ({r['url']}): {r['resumen']}")
    return "\n".join(partes)


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    _open_brave_incognito(url)
    return f"Abriendo {url} en Brave, modo incógnito."


def execute(tool_name: str, tool_input: dict) -> str:
    if tool_name == "web_search":
        return web_search(tool_input["query"])
    elif tool_name == "open_url":
        return open_url(tool_input["url"])
    else:
        return f"Herramienta desconocida: {tool_name}"
