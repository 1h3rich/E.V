"""
Modulo: ai_client.py
--------------------
Envia texto a la IA y recibe la respuesta.

E.V puede usar herramientas: acceso a ficheros, abrir aplicaciones y
terminales, escribir texto por teclado simulado, ejecutar comandos de
shell, buscar en la web y controlar musica/volumen. Cuando un comando de
shell coincide con un patron peligroso, se interrumpe la ejecucion y se
pide confirmacion hablada al usuario antes de seguir.

El bucle funciona asi:
  1. Enviamos el mensaje a Claude.
  2. Si Claude quiere usar una o mas herramientas, las ejecutamos y le
     devolvemos el resultado (pidiendo confirmacion por voz si hace falta).
  3. Repetimos hasta que Claude da una respuesta de texto final.
"""

import json
import os
import re
import time

import anthropic
import httpx
from src import config, file_tools, system_tools, web_tools, media_tools, memory_tools
from src import recorder as recorder_module
from src import stt, tts, logger, speaker_id


SYSTEM_PROMPT = (
    "Eres E.V, un asistente de voz integrado en el sistema operativo del usuario. "
    "Respondes en espanol de Espana. "
    "No eres una aplicacion que se abre y cierra: permaneces escuchando varios turnos "
    "seguidos sin que el usuario tenga que repetir el atajo de activacion. "
    "Puedes abrir aplicaciones y terminales, escribir texto donde este el cursor, "
    "ejecutar comandos del sistema, buscar en internet y abrir resultados, y controlar "
    "reproduccion multimedia y volumen, ademas de tener acceso al sistema de archivos. "
    "Usa las herramientas para obtener informacion real o ejecutar acciones reales en "
    "lugar de inventarlas o simular que las has hecho. "
    "Nunca uses sudo ni pidas al usuario contrasenas: si algo requiere privilegios de "
    "administrador, dile que lo haga el mismo manualmente. "
    "Aunque tu solo recibes texto ya transcrito, por detras SI existe reconocimiento de "
    "voz (compara la grabacion contra perfiles enrolados): identifica si quien habla es "
    "el propietario y, si no hay ninguna coincidencia con un perfil enrolado, ese turno "
    "ni siquiera te llega. Si te preguntan si reconoces la voz de quien habla, responde "
    "que si, con ese matiz, en vez de negarlo. "
    "No des rodeos. Si la pregunta es sencilla, responde breve. "
    "Si la pregunta requiere explicacion, responde paso a paso. "
    "No inventes informacion. Si no sabes algo, dilo. "
    "Tus respuestas seran leidas en voz alta: evita guiones, asteriscos, emojis "
    "y formato markdown. Escribe en prosa natural."
)

CONFIRM_RECORD_SECONDS = 4

AFFIRMATIVE_WORDS = {"si", "sí", "confirmo", "adelante", "vale", "hazlo", "dale", "ok", "okay", "correcto"}
NEGATIVE_WORDS = {"no", "cancela", "cancelar", "para", "nada", "negativo"}

_history = []

# Registro de herramientas realmente ejecutadas en la conversacion activa
# (se limpia junto con _history en clear_history()). Los mensajes intermedios
# de tool_use/tool_result NO se guardan en _history para no complicar la
# estructura entre turnos, pero eso significaba que en turnos siguientes E.V
# no tenia forma de saber con certeza que habia hecho de verdad -- confiaba
# solo en su propio resumen en texto de turnos anteriores, y a veces se
# contradecia (negaba haber abierto algo que si abrio, o al reves). Este
# registro se inyecta en el system prompt como fuente de verdad.
_session_actions = []

# Referencia opcional al ContinuousListener activo (main.py la fija con set_listener),
# para silenciarlo mientras se pide/escucha una confirmacion y no se auto-capture.
_listener = None

# Identidad del hablante del turno actual (main.py la fija en cada llamada a chat()).
# Validos durante ese turno porque main.py serializa el procesamiento con _processing:
# nunca hay dos turnos a la vez.
_current_speaker = None
_current_is_owner = False

TOOLS = file_tools.TOOLS + system_tools.TOOLS + web_tools.TOOLS + media_tools.TOOLS + memory_tools.TOOLS

_TOOL_MODULES = {}
for _mod in (file_tools, system_tools, web_tools, media_tools, memory_tools):
    for _tool in _mod.TOOLS:
        _TOOL_MODULES[_tool["name"]] = _mod


def set_listener(listener_instance) -> None:
    """Permite a main.py registrar el ContinuousListener activo."""
    global _listener
    _listener = listener_instance


def chat(text: str, speaker_name: str = None, is_owner: bool = False, on_sentence=None) -> str:
    """
    Recibe el texto del usuario y devuelve la respuesta final de la IA.
    Gestiona automaticamente el uso de herramientas si Claude las necesita.
    on_sentence(frase): si se indica, se llama con cada frase segun se genera
    (para empezar a hablar antes de que termine la respuesta completa).

    speaker_name/is_owner: identidad detectada por speaker_id.identify() para
    este turno (None/False si no hay ningun perfil de voz enrolado o no hubo match).
    """
    global _current_speaker, _current_is_owner
    _current_speaker = speaker_name
    _current_is_owner = is_owner

    _history.append({"role": "user", "content": text})

    if config.AI_PROVIDER == "ollama":
        try:
            response = _call_ollama(on_sentence)
        except httpx.ConnectError:
            if not config.ANTHROPIC_KEY:
                raise
            logger.log_error("Ollama no responde: usando Claude para este turno")
            response = _call_claude(on_sentence)
    elif config.AI_PROVIDER == "claude":
        response = _call_claude(on_sentence)
    elif config.AI_PROVIDER == "openai":
        response = _call_openai()
        if on_sentence:
            on_sentence(response)
    else:
        raise ValueError(f"Proveedor desconocido: {config.AI_PROVIDER}")

    _history.append({"role": "assistant", "content": response})

    if len(_history) > 20:
        del _history[:-20]

    logger.log_conversation(_current_speaker, _current_is_owner, text, response)

    return response


def clear_history():
    _history.clear()
    _session_actions.clear()


def _build_system_prompt() -> str:
    """Prompt base + notas de memoria guardadas + acciones reales + quien habla en este turno."""
    prompt = SYSTEM_PROMPT

    notas = memory_tools.read_memory()
    if notas:
        prompt += "\n\nNotas guardadas sobre el usuario (de conversaciones anteriores):\n" + notas

    if _session_actions:
        prompt += (
            "\n\nAcciones que YA has ejecutado de verdad en esta conversacion (fuente de "
            "verdad: si te preguntan que has hecho, respondete con esto, no lo inventes ni "
            "lo niegues):\n" + "\n".join(f"- {a}" for a in _session_actions[-15:])
        )

    if _current_speaker:
        prompt += f"\n\nAhora mismo habla: {_current_speaker}."

    return prompt


_client = None


def get_client():
    """Cliente de Anthropic reutilizado: mantiene la conexion TLS abierta entre turnos."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_KEY)
    return _client


def warm_up() -> None:
    """Carga el modelo local en VRAM (o abre la conexion TLS con Claude) antes del primer turno."""
    try:
        if config.AI_PROVIDER == "ollama":
            # Mismas opciones que un turno real (un num_ctx distinto obliga a
            # recargar el modelo) y mismo prefijo system+tools, que queda en la
            # cache de Ollama y no hay que reprocesarlo en el primer turno.
            _ollama.post("/api/chat", json={
                **_ollama_request([
                    {"role": "system", "content": _build_system_prompt()},
                    {"role": "user", "content": "hola"},
                ]),
                "stream": False,
                "options": {**_OLLAMA_OPTIONS, "num_predict": 1},
            }, timeout=120)
        elif config.AI_PROVIDER == "claude":
            get_client().models.list(limit=1)
    except Exception:
        pass  # sin red/servidor: el primer turno real dara el error util


# --- Ollama (LLM local) -------------------------------------------------------

_ollama = httpx.Client(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(120, connect=2))

# Ollama usa el formato de herramientas de OpenAI; las nuestras estan en formato Anthropic.
_OLLAMA_TOOLS = [
    {"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"],
    }}
    for t in TOOLS
]

_OLLAMA_OPTIONS = {"num_ctx": 8192, "temperature": 0.6}


def _ollama_request(messages) -> dict:
    return {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "tools": _OLLAMA_TOOLS,
        "stream": True,
        "think": False,          # qwen3 razona por defecto: lento y no hace falta aqui
        "keep_alive": -1,
        "options": _OLLAMA_OPTIONS,
    }


def _call_ollama(on_sentence=None) -> str:
    """Igual que _call_claude pero contra Ollama local (API nativa /api/chat, streaming)."""
    messages = [{"role": "system", "content": _build_system_prompt()}] + list(_history)

    for _ in range(10):  # tope de rondas de herramientas, por si el modelo entra en bucle
        splitter = _SentenceSplitter(on_sentence) if on_sentence else None
        text, tool_calls = "", []
        with _ollama.stream("POST", "/api/chat", json=_ollama_request(messages)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line).get("message", {})
                delta = msg.get("content") or ""
                if delta:
                    text += delta
                    if splitter:
                        splitter.feed(delta)
                tool_calls += msg.get("tool_calls") or []
        if splitter:
            splitter.flush()

        if not tool_calls:
            return text.strip()

        messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            print(f"  [Accediendo: {fn['name']}({next(iter(args.values()), '') if args else ''})]")
            resultado = _execute_tool(fn["name"], args)
            messages.append({"role": "tool", "tool_name": fn["name"], "content": str(resultado)})
        # El system prompt incluye las acciones ejecutadas: refrescarlo para la siguiente ronda.
        messages[0]["content"] = _build_system_prompt()

    return "He tenido que parar: demasiados pasos seguidos con herramientas."


_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")
_MIN_CHUNK = 25  # no trocear en fragmentos muy cortos: suena entrecortado


class _SentenceSplitter:
    """Acumula deltas de texto y emite frases completas por on_sentence."""

    def __init__(self, on_sentence):
        self._on_sentence = on_sentence
        self._buf = ""

    def feed(self, delta: str) -> None:
        self._buf += delta
        while True:
            m = None
            for cand in _SENTENCE_END.finditer(self._buf):
                if cand.start() >= _MIN_CHUNK:
                    m = cand
                    break
            if not m:
                return
            sentence, self._buf = self._buf[:m.start()], self._buf[m.end():]
            if sentence.strip():
                self._on_sentence(sentence)

    def flush(self) -> None:
        if self._buf.strip():
            self._on_sentence(self._buf)
        self._buf = ""


def _call_claude(on_sentence=None) -> str:
    """
    Llama a Claude con soporte de herramientas.
    Bucle: si Claude pide una herramienta -> ejecutar -> devolver resultado -> repetir.
    Si se pasa on_sentence, el texto se emite por frases segun se genera.
    """
    client = get_client()

    # Copia de trabajo para este turno.
    # Los mensajes intermedios de herramientas no se guardan en _history
    # para no contaminar el historial con bloques de contenido complejos.
    working_messages = list(_history)

    while True:
        splitter = _SentenceSplitter(on_sentence) if on_sentence else None
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_build_system_prompt(),
            tools=TOOLS,
            messages=working_messages
        ) as stream:
            for delta in stream.text_stream:
                if splitter:
                    splitter.feed(delta)
            response = stream.get_final_message()
        if splitter:
            splitter.flush()

        if response.stop_reason == "tool_use":
            # Claude quiere usar herramientas
            resultados = []

            for block in response.content:
                if block.type == "tool_use":
                    print(f"  [Accediendo: {block.name}({list(block.input.values())[0] if block.input else ''})]")
                    resultado = _execute_tool(block.name, block.input)
                    resultados.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": resultado
                    })

            # Añadimos el turno de herramienta a los mensajes de trabajo
            working_messages.append({"role": "assistant", "content": response.content})
            working_messages.append({"role": "user", "content": resultados})

        else:
            # Respuesta de texto final
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
            return text


def _call_openai() -> str:
    """Llama a la API de OpenAI (sin soporte de herramientas por ahora)."""
    try:
        import openai
    except ImportError:
        raise ImportError("Instala openai: pip install openai")

    client = openai.OpenAI(api_key=config.OPENAI_KEY)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=512,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + _history
    )

    return response.choices[0].message.content


def _execute_tool(name: str, tool_input: dict) -> str:
    """Ejecuta la herramienta indicada por Claude, gestionando confirmaciones peligrosas."""
    module = _TOOL_MODULES.get(name)
    if module is None:
        return f"Herramienta desconocida: {name}"

    try:
        resultado = module.execute(name, tool_input)
    except system_tools.NeedsConfirmation as exc:
        resultado = _confirm_dangerous_command(exc.command, exc.run)
    except Exception as e:
        logger.log_error(f"Fallo ejecutando {name}({tool_input}): {e}")
        resultado = f"Error ejecutando la herramienta: {e}"

    logger.log_tool_call(name, tool_input, resultado)

    arg = next(iter(tool_input.values()), "") if tool_input else ""
    _session_actions.append(f"{name}({arg}) -> {str(resultado)[:150]}")

    return resultado


def _confirm_dangerous_command(command: str, run) -> str:
    """
    Pide confirmacion hablada antes de ejecutar un comando que coincide con
    un patron peligroso. Silencia el listener continuo mientras dura el
    intercambio para no capturar la propia pregunta de E.V ni procesar la
    respuesta del usuario como un turno normal.

    Puerta de seguridad: si hay perfiles de voz enrolados (ver speaker_id.py),
    solo la voz del propietario puede pedir esto Y confirmarlo. Si no hay
    ningun perfil enrolado todavia, _current_is_owner es False por defecto
    pero identify() tampoco encontrara coincidencia para nadie -- para no
    bloquear a un usuario que aun no ha enrolado su voz, el gate solo aplica
    cuando SI hay al menos un perfil cargado.
    """
    hay_perfiles_enrolados = speaker_id.has_enrolled_profiles()

    if hay_perfiles_enrolados and not _current_is_owner:
        logger.log_confirmation(command, "(rechazado: quien lo pidio no es el propietario)", False)
        tts.speak("Solo el propietario puede autorizar esto.")
        return "Accion rechazada: la voz que lo pidio no es la del propietario. No se ha ejecutado el comando."

    # Si ya se esta hablando por streaming (texto previo a la herramienta),
    # esperar a que acabe para no pisar la pregunta de confirmacion.
    stream = tts.active_stream()
    if stream:
        stream.wait_idle()

    if _listener:
        _listener.mute()
    try:
        tts.speak(f"Voy a {command}. ¿Confirmas?" if command.startswith("escribir ")
                  else f"Vas a ejecutar: {command}. ¿Confirmas?")
        texto_respuesta, confirmante_es_propietario = _record_confirmation_reply()
    finally:
        if _listener:
            _listener.unmute()
            if stream:
                # El resto de la respuesta seguira hablandose: volver a mutear.
                _listener.mute(on_barge_in=tts.stop)

    if hay_perfiles_enrolados and not confirmante_es_propietario:
        logger.log_confirmation(command, texto_respuesta, False)
        tts.speak("Esa confirmación no vino de la voz del propietario. Cancelado.")
        return "La confirmación no vino de la voz del propietario. No se ha ejecutado el comando."

    aprobado = _is_affirmative(texto_respuesta)
    logger.log_confirmation(command, texto_respuesta, aprobado)

    if aprobado:
        return f"[Confirmado por el usuario] {run()}"

    tts.speak("Cancelado.")
    return "El usuario canceló la acción. No se ha ejecutado el comando."


def _record_confirmation_reply():
    """Graba una respuesta corta y devuelve (texto_transcrito, es_propietario)."""
    rec = recorder_module.AudioRecorder()
    rec.start()
    time.sleep(CONFIRM_RECORD_SECONDS)
    audio_path = rec.stop()

    if not audio_path:
        return "", False

    try:
        texto = stt.transcribe(audio_path)
        _, es_propietario, _ = speaker_id.identify(audio_path)
        return texto, es_propietario
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


def _is_affirmative(text: str) -> bool:
    tokens = set(text.lower().replace(",", " ").replace(".", " ").split())
    if tokens & NEGATIVE_WORDS:
        return False
    return bool(tokens & AFFIRMATIVE_WORDS)
