# Receita usada pelo Coolify para rodar o Monitor de IA.
# Base com Node (para instalar a CLI do Codex) + Python (para rodar o painel).
FROM node:20-slim

# Python 3 (o painel usa so a biblioteca padrao) e certificados
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CLI oficial do Codex (necessaria para o login "celular/QR" do Codex)
RUN npm install -g @openai/codex

WORKDIR /app

# Copia o codigo do painel para dentro do container
COPY app.py qrcode.min.js ./
COPY assets ./assets

# Onde ficam config.json e backups (volume persistente montado pelo Coolify)
ENV IA_MONITOR_CONFIG=/data/config.json
# Escuta em todas as interfaces para o Coolify conseguir acessar
ENV IA_MONITOR_HOST=0.0.0.0
ENV IA_MONITOR_PORT=8765

# Pasta de dados que sera persistida
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

CMD ["python3", "app.py"]
