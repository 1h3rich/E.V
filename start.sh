#!/bin/bash
# Script de arranque del Asistente de Voz.

cd "$(dirname "$(readlink -f "$0")")"

# Usamos el Python del venv directamente, sin activarlo
# Esto evita problemas con fish shell y es mas robusto
venv/bin/python main.py
