#!/usr/bin/env bash
# Monitor de IA - Linux
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    exec python3 app.py
elif command -v python >/dev/null 2>&1; then
    exec python app.py
else
    echo "Python 3 nao encontrado. Instale com: sudo apt install python3"
    read -r -p "Pressione Enter para sair..."
fi
