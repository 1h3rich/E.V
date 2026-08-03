"""
enroll_voice.py — Enrolamiento de voz para E.V
-------------------------------------------------
Script de uso MANUAL. No es una herramienta que E.V pueda invocar por voz:
si lo fuera, cualquiera podria decirle "aprende mi voz" y auto-registrarse
con privilegios de propietario.

Uso (desde la carpeta del proyecto):
    venv/bin/python enroll_voice.py owner      # la voz autorizada (gate de seguridad)
    venv/bin/python enroll_voice.py Ana        # perfil solo para personalizacion

Graba varias muestras cortas de voz, calcula un embedding con resemblyzer
(promediando las muestras) y lo guarda en data/voices/<nombre>.npy.
"""

import os
import sys
import time

from src.recorder import AudioRecorder

NUM_SAMPLES = 3
SAMPLE_SECONDS = 5

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "voices")


def main():
    if len(sys.argv) != 2:
        print("Uso: venv/bin/python enroll_voice.py <nombre>")
        print("  'owner' = la voz autorizada para acciones sensibles")
        print("  cualquier otro nombre = perfil solo de personalizacion")
        sys.exit(1)

    nombre = sys.argv[1].strip()
    if not nombre:
        print("El nombre no puede estar vacio.")
        sys.exit(1)

    print(f"\nVamos a grabar {NUM_SAMPLES} muestras de {SAMPLE_SECONDS}s para el perfil '{nombre}'.")
    print("Habla de forma natural, como si hablases con E.V normalmente.\n")

    print("Cargando el modelo de voz (puede tardar unos segundos la primera vez)...")
    from resemblyzer import VoiceEncoder, preprocess_wav
    encoder = VoiceEncoder()

    wavs = []
    recorder = AudioRecorder()

    for i in range(1, NUM_SAMPLES + 1):
        input(f"\nPulsa ENTER y empieza a hablar (muestra {i}/{NUM_SAMPLES})...")
        recorder.start()
        for restante in range(SAMPLE_SECONDS, 0, -1):
            print(f"  Grabando... {restante}s", end="\r")
            time.sleep(1)
        audio_path = recorder.stop()
        print("  Muestra grabada.               ")

        if not audio_path:
            print("  No se detecto audio, repite esta muestra.")
            continue

        wavs.append(preprocess_wav(audio_path))
        os.remove(audio_path)

    if len(wavs) < 2:
        print("\nNo hay suficientes muestras validas. Prueba de nuevo.")
        sys.exit(1)

    print("\nCalculando el perfil de voz...")
    embed = encoder.embed_speaker(wavs)

    os.makedirs(VOICES_DIR, exist_ok=True)
    destino = os.path.join(VOICES_DIR, f"{nombre}.npy")

    import numpy as np
    np.save(destino, embed)

    print(f"\nListo. Perfil guardado en {destino}")
    if nombre == "owner":
        print("Este es el perfil de PROPIETARIO: solo esta voz podra autorizar acciones sensibles.")
    else:
        print("Este perfil es solo para personalizacion (sin privilegios especiales).")

    print("\nReinicia E.V para que cargue el nuevo perfil: systemctl --user restart ivy.service")


if __name__ == "__main__":
    main()
