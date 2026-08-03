"""
Modulo: hotkey.py
-----------------
Escucha combinaciones de teclas de forma global. "Global" significa que
funciona aunque la ventana del programa este en segundo plano.

El atajo es configurable via HOTKEY_MODIFIER y HOTKEY_KEY en .env (ver
src/config.py) -- por defecto Super+Y. HOTKEY_MODIFIER admite combinarse
con "+", por ejemplo "ctrl+alt" o "ctrl+shift".

Cuando el usuario pulsa el atajo, se llama a la funcion `callback` que le
pasemos.
"""

from pynput import keyboard

MODIFIER_KEYS = {
    "super": (keyboard.Key.cmd_l, keyboard.Key.cmd_r, keyboard.Key.cmd),
    "ctrl": (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl),
    "alt": (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt),
    "shift": (keyboard.Key.shift_l, keyboard.Key.shift_r, keyboard.Key.shift),
}


class HotkeyListener:
    """
    Escucha una combinacion de teclas (modificador(es) + tecla) en todo el
    sistema operativo. Cuando se detecta el atajo completo, llama a la
    funcion `callback`.
    """

    def __init__(self, callback, modifiers=("super",), key="y"):
        modifiers_normalizados = tuple(m.strip().lower() for m in modifiers if m.strip())
        desconocidos = [m for m in modifiers_normalizados if m not in MODIFIER_KEYS]
        if desconocidos:
            raise ValueError(
                f"Modificador de atajo desconocido: {desconocidos}. "
                f"Validos: {list(MODIFIER_KEYS)}"
            )
        if not modifiers_normalizados:
            raise ValueError("HOTKEY_MODIFIER no puede estar vacio.")

        self._callback = callback
        self._required_modifiers = set(modifiers_normalizados)
        self._key = key.strip().lower()

        # Modificadores actualmente pulsados (de entre los requeridos).
        self._pressed_modifiers = set()

        # Evita que la auto-repeticion de teclas del sistema operativo (si
        # mantienes la tecla pulsada, el SO manda "pulsada" varias veces
        # seguidas) dispare el callback multiples veces por una sola pulsacion.
        self._key_pressed = False

        # El objeto que escucha el teclado (se crea al llamar start())
        self._listener = None

    def start(self):
        """Inicia la escucha del teclado en un hilo separado."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )
        self._listener.start()

    def stop(self):
        """Detiene la escucha del teclado."""
        if self._listener:
            self._listener.stop()

    def _modifier_name(self, key):
        for nombre, variantes in MODIFIER_KEYS.items():
            if key in variantes:
                return nombre
        return None

    def _on_press(self, key):
        """Se ejecuta cada vez que se pulsa una tecla."""
        nombre = self._modifier_name(key)
        if nombre is not None:
            if nombre in self._required_modifiers:
                self._pressed_modifiers.add(nombre)
            return

        # Solo dispara si TODOS los modificadores requeridos estan pulsados.
        if self._pressed_modifiers != self._required_modifiers:
            return

        try:
            if key.char and key.char.lower() == self._key and not self._key_pressed:
                self._key_pressed = True
                self._callback()
        except AttributeError:
            # Teclas especiales (F1, ESC, etc.) no tienen .char
            pass

    def _on_release(self, key):
        """Se ejecuta cada vez que se suelta una tecla."""
        nombre = self._modifier_name(key)
        if nombre is not None:
            self._pressed_modifiers.discard(nombre)
            return

        try:
            if key.char and key.char.lower() == self._key:
                self._key_pressed = False
        except AttributeError:
            pass
