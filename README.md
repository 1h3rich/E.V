# E.V — Asistente de voz

Asistente de voz que corre en segundo plano (servicio systemd, sin ventana).
Escucha con un atajo de teclado, transcribe lo que dices, se lo pasa a
Claude (o OpenAI) y te contesta hablando.

## Cómo se usa

1. Pulsa el atajo de teclado configurado (por defecto **Super+Y**, tecla
   Windows/Meta + Y — se puede cambiar, ver "Configuración" más abajo). E.V
   empieza a escuchar en continuo — no hace falta repulsar la tecla en cada
   turno.
2. Habla con normalidad. E.V transcribe, responde y sigue escuchando
   automáticamente para el siguiente turno.
3. Para desactivarla di **"cállate"**, "para", "silencio" o "basta" (frase
   corta, máximo 4 palabras). También puedes volver a pulsar el atajo.
4. Para parar el servicio del todo: `systemctl --user stop ivy`.

Si hablas y E.V no responde nada, lo primero que hay que comprobar es la
**voz enrolada** (siguiente sección) — es la causa más habitual.

## Cómo funciona

E.V es un pipeline de varios pasos encadenados, todo corriendo en local
salvo la llamada a la IA:

```
Atajo de teclado (src/hotkey.py)
        │  activa/desactiva la escucha
        ▼
Grabación continua (src/listener.py + src/recorder.py)
        │  detecta cuándo hablas y cuándo paras (turnos)
        ▼
Transcripción local (src/stt.py, Whisper vía faster-whisper)
        │  audio -> texto, sin enviar el audio a ningún sitio
        ▼
Identificación de voz (src/speaker_id.py, opcional)
        │  compara el audio contra data/voices/*.npy
        ▼
IA (src/ai_client.py) ── aquí se decide qué responder y qué hacer
        │
        ▼
Voz de salida (src/tts.py, Piper local)
```

**Qué IA usa y cómo**: por defecto, Claude (`claude-haiku-4-5`) a través de
la API de mensajes de Anthropic, en `src/ai_client.py` (función
`_call_claude`). El texto transcrito se manda como mensaje de usuario junto
con un *system prompt* que le dice a la IA quién es (E.V), cómo debe
responder (español de España, en prosa, sin markdown porque se lee en voz
alta) y qué herramientas tiene disponibles. También se puede usar OpenAI
(`gpt-4o-mini`) cambiando `AI_PROVIDER`, pero esa vía **no** tiene soporte
de herramientas todavía — solo conversa.

Claude puede pedir usar **herramientas** (definidas en `src/file_tools.py`,
`src/system_tools.py`, `src/web_tools.py`, `src/media_tools.py` y
`src/memory_tools.py`: abrir apps, ejecutar comandos, buscar en la web,
controlar música/volumen, leer/guardar notas...). El bucle en `_call_claude`
es: se envía el mensaje → si la IA pide una herramienta, se ejecuta en local
y se le devuelve el resultado → se repite hasta que la IA da una respuesta
de texto final, que es la que se lee en voz alta. Los comandos de shell
peligrosos no se ejecutan directamente: ver "Comandos peligrosos y
privilegios" más abajo.

E.V mantiene un historial de conversación en memoria (últimos 20 mensajes,
se olvida todo al desactivarse) y una nota persistente en `data/memory.md`
para cosas que le pidas recordar explícitamente entre sesiones.

## Reconocimiento de voz (importante)

E.V puede reconocer quién habla. Este comportamiento cambia según si hay
perfiles de voz guardados en `data/voices/`:

- **Sin ningún perfil enrolado** (instalación nueva): E.V responde a
  cualquier voz, sin distinción.
- **En cuanto se enrola al menos un perfil**: E.V solo responde a las voces
  que reconoce. Cualquier voz sin perfil se ignora **en silencio** (no hay
  aviso ni sonido — parece que E.V no funciona, pero es este filtro).
- Dentro de las voces reconocidas hay dos niveles:
  - **`owner`** (propietario): conversa con normalidad y además puede
    autorizar acciones sensibles (comandos que pueden borrar/modificar
    cosas). Solo debe haber un `owner` por instalación.
  - **Cualquier otro nombre**: conversa con normalidad, pero no puede
    autorizar acciones sensibles — E.V responderá "Solo el propietario
    puede autorizar esto."

### Enrolar una voz nueva (por ejemplo, para que otra persona la use)

```bash
cd ~/Proyectos/IvyAI/ivy
venv/bin/python enroll_voice.py <nombre>
```

- `venv/bin/python enroll_voice.py owner` — enrola la voz del propietario
  de esta instalación (privilegios completos). Solo debería hacerse una vez
  por instalación; volver a ejecutarlo sobrescribe el perfil `owner`
  existente.
- `venv/bin/python enroll_voice.py Ana` (o cualquier otro nombre) — enrola
  una voz adicional que podrá conversar pero sin privilegios de propietario.

El script pide 3 muestras de voz de 5 segundos hablando con naturalidad.
Tras enrolar, reinicia el servicio para que cargue el perfil nuevo:

```bash
systemctl --user restart ivy.service
```

Los perfiles se guardan en `data/voices/<nombre>.npy`. Para quitar un
perfil, basta con borrar su archivo `.npy` y reiniciar el servicio.

## Instalación

Requisitos: Python 3, `mpv` (reproductor de audio, suele venir instalado en
CachyOS), una API key de Anthropic (o de OpenAI si usas ese proveedor).

```bash
cd ~/Proyectos/IvyAI/ivy
python -m venv venv

# IMPORTANTE: instalar primero torch CPU-only (si no, pip instala la build
# con CUDA y arrastra ~3GB de paquetes nvidia-* innecesarios en GPU AMD)
venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu torch
venv/bin/python -m pip install -r requirements.txt
```

Copia la plantilla de configuración y rellena tu API key:

```bash
cp env.example .env
# edita .env y pon tu ANTHROPIC_KEY (o tu OPENAI_KEY si usas AI_PROVIDER=openai)
```

Descarga la voz de Piper (síntesis de voz local) en `data/piper/` — ver
sección "Cambiar la voz (TTS)" más abajo para el enlace y el nombre exacto
del modelo que usa E.V por defecto.

Enrola tu voz como propietario (ver sección anterior):

```bash
venv/bin/python enroll_voice.py owner
```

Instala el servicio systemd para que arranque en segundo plano. `systemd`
no expande `~`, así que hay que poner la ruta absoluta a esta carpeta:

```bash
mkdir -p ~/.config/systemd/user
sed "s#/ruta/a/ivy#$(pwd)#g" ivy.service.example > ~/.config/systemd/user/ivy.service
systemctl --user daemon-reload
systemctl --user enable --now ivy.service
```

(`ivy.service.example` es la plantilla incluida en el repo; el `sed` de
arriba sustituye `/ruta/a/ivy` por la carpeta desde la que lo ejecutes.)

## Configuración (`.env`)

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ANTHROPIC_KEY` | (vacío) | API key de Claude. Obligatoria si `AI_PROVIDER=claude`. |
| `OPENAI_KEY` | (vacío) | API key de OpenAI. Solo si `AI_PROVIDER=openai`. |
| `AI_PROVIDER` | `claude` | `claude` u `openai`. |
| `WHISPER_MODEL` | `base` | Modelo local de Whisper para transcribir. Modelos más grandes (`small`, `medium`...) transcriben mejor pero tardan más y usan más RAM. |
| `LANGUAGE` | `es` | Idioma de transcripción. |
| `VOICE_MATCH_THRESHOLD` | `0.65` | Similitud mínima (0-1) para reconocer una voz enrolada. Si a alguien no le reconoce bien la voz, prueba a bajarlo un poco; si reconoce a quien no debe, súbelo. |
| `HOTKEY_MODIFIER` | `super` | Modificador del atajo de activación: `super`, `ctrl`, `alt`, `shift`, o combinados con `+` (ej. `ctrl+alt`). |
| `HOTKEY_KEY` | `y` | Letra del atajo de activación (se combina con `HOTKEY_MODIFIER`). |

Tras cambiar cualquier valor: `systemctl --user restart ivy.service`.

## Qué se puede personalizar

- **Atajo de activación**: no hay wake-word por voz, se activa por atajo de
  teclado. Se cambia con `HOTKEY_MODIFIER` y `HOTKEY_KEY` en `.env` (por
  defecto `super` + `y`), sin tocar código. Modificadores admitidos:
  `super`, `ctrl`, `alt`, `shift`, y combinaciones con `+` (ej.
  `ctrl+alt`).
- **Frases de parada**: lista `STOP_PHRASES` en `main.py` (por defecto
  "cállate", "para", "deja de escuchar", "silencio", "basta", "ya está").
- **Cambiar la voz (TTS)**: E.V usa Piper local, voz `es_ES-sharvard-medium`
  (el mismo modelo incluye una voz masculina y otra femenina). Para cambiar:
  1. Descarga otro modelo `.onnx` + `.onnx.json` de
     https://huggingface.co/rhasspy/piper-voices en `data/piper/`.
  2. Edita `_MODEL_PATH` en `src/tts.py` para apuntar al nuevo archivo.
  3. Si el modelo es multi-hablante, ajusta `_SPEAKER_ID` (0 = masculina,
     1 = femenina en el modelo actual; consulta el `.onnx.json` del modelo
     para ver sus hablantes disponibles).
- **Cambiar de proveedor de IA**: pon `AI_PROVIDER=openai` en `.env` y
  añade `OPENAI_KEY`. El modelo de Claude usado está fijado en
  `src/ai_client.py` (`_call_claude`, actualmente `claude-haiku-4-5`).
- **Memoria**: E.V puede recordar cosas que le pidas ("recuerda que...",
  "olvida que..."). Se guarda en texto plano en `data/memory.md` — se puede
  editar o vaciar ese archivo a mano.
- **Comandos peligrosos y privilegios**: ver la sección dedicada más abajo.

## Comandos peligrosos y privilegios

E.V puede ejecutar comandos, abrir aplicaciones y escribir texto por ti
(`src/system_tools.py`). El modelo de seguridad está tomado del fork
[Aria](https://gitlab.com/Relieving/aria) y ampliado:

1. **Nunca hay shell.** El comando se parte con `shlex` y se ejecuta como
   lista de argumentos (`shell=False`). No hay tuberías, redirecciones, `;`,
   `&&`, variables ni `$(...)`: si el texto los trae, se rechaza. Antes se
   usaba `bash -c` con una lista negra de expresiones regulares, y trucos
   como `s\udo` o `$(printf 'sud\157')` se la saltaban.

2. **Lista blanca, no lista negra.** Solo se ejecuta lo listado en
   `src/system_tools.py`:
   - `SOLO_LECTURA` (`ls`, `cat`, `grep`, `find`, `df`, `ps`, `journalctl`…):
     directos.
   - `MODIFICAN` (`rm`, `mv`, `cp`, `mkdir`, `chmod`, `kill`…): piden
     confirmación hablada.
   - `SUBCOMANDOS` (`git`, `systemctl`, `docker`, `flatpak`, `pacman`): los de
     consulta van directos y el resto se confirma.
   - Todo lo demás se rechaza **por omisión**: `sudo`, `su`, `pkexec`, `bash`,
     `python`, `curl`, `sed`, `awk`…
   - `ARGS_PROHIBIDOS` veta argumentos que convierten un comando permitido en
     ejecución o escritura arbitraria (`find -exec`, `fd -x`, `git -c`,
     `sort -o`, `tar -I`…).

   Si añades un binario a `SOLO_LECTURA`, comprueba antes que no puede ejecutar
   código ni escribir ficheros. Tras tocar las listas, pasa
   `venv/bin/python tests/test_lista_blanca.py`.

   `type_text` **siempre** pide confirmación: con una terminal enfocada,
   teclear equivale a ejecutar cualquier cosa.

   La confirmación funciona así: E.V pregunta en voz alta "Vas a ejecutar:
   `<comando>`. ¿Confirmas?" y solo lo lanza si quien confirma es la voz del
   **propietario** (perfil `owner`). Si nadie ha enrolado ninguna voz, puede
   confirmar cualquiera.

3. **Quién puede confirmar**: lo decide `_current_is_owner` en
   `src/ai_client.py`, que se rellena con el resultado de
   `speaker_id.identify()` en cada turno. Si quieres que además de `owner`
   otro perfil enrolado pueda confirmar comandos peligrosos, hay que tocar
   el check correspondiente en `_confirm_dangerous_command`
   (`src/ai_client.py`) para que acepte también esos nombres.

## Comandos útiles

```bash
systemctl --user status ivy.service    # ver si está corriendo
systemctl --user restart ivy.service   # aplicar cambios de config/código
systemctl --user stop ivy.service      # apagar
journalctl --user -u ivy.service -f    # ver logs en vivo (útil para depurar)
```

Para ejecutar a mano en una terminal (depuración, ver salida por consola en
directo):

```bash
systemctl --user stop ivy.service
cd ~/Proyectos/IvyAI/ivy
venv/bin/python main.py
```

## Problemas comunes

- **No responde a nadie**: revisa `systemctl --user status ivy.service` — si
  no está `active (running)`, mira `journalctl --user -u ivy.service -e`
  para el error (falta `.env`, falta la API key, falta el modelo de Piper...).
- **Responde a ti pero no a otra persona**: esa persona no tiene voz
  enrolada — sigue "Enrolar una voz nueva" más arriba.
- **Deja de reconocer una voz que antes sí reconocía**: prueba a bajar
  `VOICE_MATCH_THRESHOLD` en `.env` (micrófono distinto, más ruido de fondo,
  etc. bajan el score).
- **No hay sonido de respuesta**: comprueba que `mpv` está instalado
  (`mpv --version`).

## Licencia

MIT-0 (ver [LICENSE](LICENSE)). En cristiano: libre uso total, me la pela lo que
hagáis con este repositorio. Copiadlo, cambiadlo, vendedlo o lo que os dé la gana,
sin pedir permiso ni tener que mencionarme. Eso sí, si algo se rompe no es
problema mío.
