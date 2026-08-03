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

import os
import time

import anthropic
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


def chat(text: str, speaker_name: str = None, is_owner: bool = False) -> str:
    """
    Recibe el texto del usuario y devuelve la respuesta final de la IA.
    Gestiona automaticamente el uso de herramientas si Claude las necesita.

    speaker_name/is_owner: identidad detectada por speaker_id.identify() para
    este turno (None/False si no hay ningun perfil de voz enrolado o no hubo match).
    """
    global _current_speaker, _current_is_owner
    _current_speaker = speaker_name
    _current_is_owner = is_owner

    _history.append({"role": "user", "content": text})

    if config.AI_PROVIDER == "claude":
        response = _call_claude()
    elif config.AI_PROVIDER == "openai":
        response = _call_openai()
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


def _call_claude() -> str:
    """
    Llama a Claude con soporte de herramientas.
    Bucle: si Claude pide una herramienta -> ejecutar -> devolver resultado -> repetir.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_KEY)

    # Copia de trabajo para este turno.
    # Los mensajes intermedios de herramientas no se guardan en _history
    # para no contaminar el historial con bloques de contenido complejos.
    working_messages = list(_history)

    while True:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_build_system_prompt(),
            tools=TOOLS,
            messages=working_messages
        )

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
        resultado = _confirm_dangerous_command(exc.command)
    except Exception as e:
        logger.log_error(f"Fallo ejecutando {name}({tool_input}): {e}")
        resultado = f"Error ejecutando la herramienta: {e}"

    logger.log_tool_call(name, tool_input, resultado)

    arg = next(iter(tool_input.values()), "") if tool_input else ""
    _session_actions.append(f"{name}({arg}) -> {str(resultado)[:150]}")

    return resultado


def _confirm_dangerous_command(command: str) -> str:
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

    if _listener:
        _listener.mute()
    try:
        tts.speak(f"Vas a ejecutar: {command}. ¿Confirmas?")
        texto_respuesta, confirmante_es_propietario = _record_confirmation_reply()
    finally:
        if _listener:
            _listener.unmute()

    if hay_perfiles_enrolados and not confirmante_es_propietario:
        logger.log_confirmation(command, texto_respuesta, False)
        tts.speak("Esa confirmación no vino de la voz del propietario. Cancelado.")
        return "La confirmación no vino de la voz del propietario. No se ha ejecutado el comando."

    aprobado = _is_affirmative(texto_respuesta)
    logger.log_confirmation(command, texto_respuesta, aprobado)

    if aprobado:
        return f"[Confirmado por el usuario] {system_tools._execute_shell(command)}"

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
