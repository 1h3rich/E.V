"""
main.py — Punto de entrada de E.V
-----------------------------------
E.V corre como servicio de fondo (systemd --user). No abre ninguna ventana.

Flujo:
  1. El usuario pulsa el atajo configurado (HOTKEY_MODIFIER+HOTKEY_KEY en
     .env, por defecto Super+Y -- ver src/hotkey.py y src/config.py).
  2. E.V empieza a escuchar en continuo (varios turnos, sin repulsar la tecla).
  3. Cada turno de habla se transcribe, se envia a Claude, y la respuesta se
     dice en voz alta. E.V sigue escuchando automaticamente para el siguiente
     turno sin que el usuario tenga que hacer nada mas.
  4. El usuario dice "callate" (o similar) -> E.V responde "De acuerdo" y deja
     de escuchar. Volver a pulsar el atajo tambien desactiva manualmente, por
     si el reconocimiento de la frase de parada falla.

Para salir del proceso: systemctl --user stop ivy (o CTRL+C si se ejecuta
a mano en una terminal para depurar).
"""

import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src.hotkey import HotkeyListener
from src.listener import ContinuousListener
from src import config, stt, ai_client, tts, logger, speaker_id


def _hotkey_label() -> str:
    modificadores = "+".join(m.capitalize() for m in config.HOTKEY_MODIFIER.split("+"))
    return f"{modificadores}+{config.HOTKEY_KEY.upper()}"


HOTKEY_LABEL = _hotkey_label()


STOP_PHRASES = ["callate", "cállate", "deja de escuchar", "para", "silencio", "basta", "ya esta", "ya está"]

_active = False
_processing = False  # evita procesar un turno nuevo mientras aun se resuelve/habla el anterior


def _is_stop_phrase(text: str) -> bool:
    """
    "para" es una de las palabras mas comunes del espanol (aparece en
    cualquier frase con "para que...", "para mejorar...", etc.), asi que
    una comparacion por substring dispara falsos positivos y desactiva a
    E.V en medio de una conversacion normal. Para evitarlo: solo se
    considera orden de parada si la frase completa es corta (maximo 4
    palabras, dejando margen para "vale, para ya" o similar) Y contiene la
    palabra/frase de parada como palabra completa, no como fragmento de
    otra palabra u oracion mas larga.
    """
    normalized = text.lower().strip()
    palabras = normalized.split()
    if len(palabras) > 4:
        return False
    return any(re.search(rf"\b{re.escape(frase)}\b", normalized) for frase in STOP_PHRASES)


def _cleanup(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def _notify(mensaje: str) -> None:
    """
    Notificacion del sistema (org.freedesktop.Notifications) al activar o
    desactivar la escucha continua. spider-notify.py la intercepta y la
    muestra; ver memoria spider-notify.
    """
    try:
        subprocess.Popen(
            ["notify-send", "E.V", mensaje],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        pass


def _on_utterance(audio_path: str) -> None:
    """Se llama (en un hilo) cada vez que listener.py cierra un turno de habla."""
    global _processing

    if _processing:
        # E.V ya esta resolviendo un turno anterior: descartamos este audio
        # en vez de encolarlo, para mantener el flujo simple.
        _cleanup(audio_path)
        return

    _processing = True
    try:
        # STT e identificacion de voz son independientes: en paralelo, el
        # tiempo es el del mas lento en vez de la suma.
        fut_speaker = _pool.submit(speaker_id.identify, audio_path)
        texto = stt.transcribe(audio_path)
        speaker_name, is_owner, score = fut_speaker.result()
    finally:
        _cleanup(audio_path)

    if not texto:
        _processing = False
        return

    if speaker_id.has_enrolled_profiles() and speaker_name is None:
        # Hay perfiles enrolados pero esta voz no coincide con ninguno (un
        # video de fondo, alguien sin perfil, etc.): se ignora en silencio en
        # vez de tratarlo como una orden. Un perfil enrolado que SI coincide
        # (sea "owner" o cualquier otro nombre) sigue adelante y conversa
        # normal; los privilegios de propietario para acciones peligrosas se
        # comprueban aparte en ai_client._confirm_dangerous_command.
        # Se registra el score (similitud coseno 0-1) para poder calibrar
        # VOICE_MATCH_THRESHOLD con datos reales en vez de a ojo.
        print(f"\n  [Ignorado, voz no reconocida (score={score:.3f}): {texto}]\n")
        _processing = False
        return

    if speaker_id.has_enrolled_profiles():
        print(f"  [Voz reconocida como {speaker_name}, score={score:.3f}]")

    print(f"\n  Tu: {texto}\n")

    if _is_stop_phrase(texto):
        _deactivate(despedida=True)
        _processing = False
        return

    stream = None
    try:
        # Se mutea antes de llamar al LLM: la voz empieza a salir por frases
        # mientras la respuesta aun se esta generando (streaming).
        listener.mute(on_barge_in=tts.stop)
        stream = tts.start_stream()
        respuesta = ai_client.chat(
            texto, speaker_name=speaker_name, is_owner=is_owner, on_sentence=stream.say
        )
        print(f"  E.V: {respuesta}\n")
        stream.finish()
    except Exception as e:
        logger.log_error(f"Error procesando turno: {e}")
        print(f"  [Error: {e}]")
        if stream:
            stream.cancel()
            stream.finish()
    finally:
        listener.unmute()
        _processing = False


listener = ContinuousListener(_on_utterance)
ai_client.set_listener(listener)

_pool = ThreadPoolExecutor(max_workers=2)


def _preload() -> None:
    """
    Carga Whisper, Piper, el codificador de voz y la conexion a la API en un
    hilo al arrancar el servicio, para que el primer turno no pague ~3 s de
    cargas perezosas. Cuesta algo de RAM en reposo a cambio de rapidez.
    """
    t0 = time.time()
    for nombre, fn in (("stt", stt.warm_up), ("tts", tts.warm_up),
                       ("voz", speaker_id.warm_up), ("api", ai_client.warm_up)):
        try:
            fn()
        except Exception as e:
            logger.log_error(f"Precarga de {nombre} fallo: {e}")
    print(f"  [Modelos precargados en {time.time() - t0:.1f}s]")


def _activate() -> None:
    global _active
    if _active:
        return
    _active = True
    ai_client.clear_history()
    listener.start()
    _notify("Activada. Escuchando...")
    print("\n  E.V escuchando. Habla cuando quieras, di 'callate' para desactivar.\n")


def _deactivate(despedida: bool = False) -> None:
    global _active
    if not _active:
        return
    _active = False
    if despedida:
        listener.mute()
        tts.speak("De acuerdo.")
    listener.stop()
    ai_client.clear_history()
    _notify("Desactivada.")
    print(f"  E.V inactiva. Pulsa {HOTKEY_LABEL} para activarla.\n")


def on_hotkey() -> None:
    if _active:
        _deactivate()
    else:
        _activate()


def main():
    print()
    print("=" * 50)
    print("  E.V")
    print("=" * 50)
    print()
    print(f"  Pulsa {HOTKEY_LABEL}  para activar la escucha continua")
    print("  Di 'callate' / 'para' / 'silencio' para desactivarla")
    print("  Pulsa CTRL+C  para salir")
    print()

    hotkey_listener = HotkeyListener(
        on_hotkey,
        modifiers=config.HOTKEY_MODIFIER.split("+"),
        key=config.HOTKEY_KEY
    )
    hotkey_listener.start()
    threading.Thread(target=_preload, daemon=True).start()

    print("  Lista. Esperando...\n")

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n  Saliendo.")
        _deactivate()
        hotkey_listener.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
