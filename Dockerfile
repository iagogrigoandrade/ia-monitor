# Receita usada pelo Coolify para rodar o Monitor de IA.
# Usa somente Python (o app nao depende de bibliotecas externas).
FROM python:3.12-slim

WORKDIR /app

# Copia o codigo do painel para dentro do container
COPY app.py qrcode.min.js ./

# Onde ficam config.json e backups (volume persistente montado pelo Coolify)
ENV IA_MONITOR_CONFIG=/data/config.json
# Escuta em todas as interfaces para o Coolify conseguir acessar
ENV IA_MONITOR_HOST=0.0.0.0
ENV IA_MONITOR_PORT=8765

# Pasta de dados que sera persistida
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8765

CMD ["python", "app.py"]
