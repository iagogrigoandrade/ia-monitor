#!/usr/bin/env bash
# Monitor de IA - macOS (duplo clique para abrir)
cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    exec python3 app.py
elif command -v python >/dev/null 2>&1; then
    exec python app.py
else
    echo "Python 3 nao encontrado."
    echo "Instale digitando no Terminal: xcode-select --install"
    read -r -p "Pressione Enter para sair..."
fi
