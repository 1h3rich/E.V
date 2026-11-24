"""
Pruebas de la lista blanca de run_shell_command (src/system_tools.py).
Ejecutar: venv/bin/python tests/test_lista_blanca.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.system_tools import ComandoRechazado, NeedsConfirmation, _clasificar, run_shell_command

RECHAZADOS = [
    "sudo rm -rf /", "s\\udo ls", '"su""do" ls', "$(printf 'sud\\157') ls", "su -c ls",
    "pkexec ls", "bash -c ls", "python -c 'print(1)'", "sh script.sh",
    "ls; rm -rf ~", "ls && rm x", "ls | sh", "cat /dev/zero > /dev/nvme0n1",
    "echo `id`", "echo $HOME", "/usr/bin/rm -rf x", "./script.sh",
    "find . -exec rm {} +", "find . -delete", "find . -fprint /tmp/x",
    "fd -x rm", "fd -Hx rm", "fd --exec=rm", "git -c core.pager=sh log",
    "git diff --output=/tmp/x", "sort -o /etc/x a", "sort -uo x a", "sort --output=x a",
    "tar -I sh -xf a.tar", "tree -o x", "rg --pre sh foo", "journalctl --vacuum-size=1",
    "uniq entrada salida", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1",
    "shutdown now", "curl http://x", "wget http://x", "awk 'BEGIN{system(\"id\")}'",
    "sed -i s/a/b/ f", "env sh", "xargs rm", "nohup sh", "",
]

CONFIRMAN = [
    "rm -rf ~/x", "rm -r -f ~/x", "mv a b", "cp a b", "mkdir x", "chmod 755 x", "kill 1234",
    "git commit -m x", "git push --force", "git branch -D main", "git branch nueva",
    "git tag -d v1", "git remote add o url", "git -C repo status",
    "systemctl --user stop ivy", "systemctl restart x", "pacman -Syu",
    "ip link set eth0 down", "docker rm x",
]

DIRECTOS = [
    "ls -la ~/Descargas", "cat README.md", "grep -rn 'foo.*bar' src", "find . -name '*.py'",
    "df -h", "free -g", "ps aux", "git status", "git log --oneline -5", "git branch -a",
    "git branch", "systemctl --user status ivy", "journalctl --user -u ivy -n 20",
    "pacman -Qi mpv", "ip addr", "fd -H foo", "sort -u a", "echo hola", "uptime",
]

fallos = []

for c in RECHAZADOS:
    try:
        _clasificar(c)
        fallos.append(f"DEBIA RECHAZAR: {c!r}")
    except ComandoRechazado:
        pass

for c in CONFIRMAN:
    try:
        _, conf = _clasificar(c)
        if not conf:
            fallos.append(f"DEBIA CONFIRMAR: {c!r}")
    except ComandoRechazado as e:
        fallos.append(f"RECHAZADO Y DEBIA CONFIRMAR: {c!r} ({e})")

for c in DIRECTOS:
    try:
        _, conf = _clasificar(c)
        if conf:
            fallos.append(f"PIDE CONFIRMACION Y NO DEBIA: {c!r}")
    except ComandoRechazado as e:
        fallos.append(f"RECHAZADO Y DEBIA PASAR: {c!r} ({e})")

# Nada se ejecuta sin confirmar, y run() ejecuta la accion real.
try:
    run_shell_command("rm /tmp/no-existe-ivy-test")
    fallos.append("rm se ejecuto sin confirmar")
except NeedsConfirmation as exc:
    if "Codigo de salida: 1" not in exc.run():
        fallos.append("run() de la confirmacion no ejecuto el comando")

# ~ se expande y se ejecuta sin shell.
if "Codigo de salida: 0" not in run_shell_command("ls ~"):
    fallos.append("ls ~ no funciono")

total = len(RECHAZADOS) + len(CONFIRMAN) + len(DIRECTOS) + 2
print("\n".join(fallos) if fallos else f"OK: {total} casos")
sys.exit(1 if fallos else 0)
