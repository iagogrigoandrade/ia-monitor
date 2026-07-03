# Monitor de IA

Painel web simples para acompanhar limites e saldos de serviços de IA em um só lugar:

- Claude: limite de 5h e semanal
- Codex / ChatGPT: limite de 5h e semanal
- DeepSeek: saldo
- OpenRouter: créditos

O projeto roda localmente com Python 3 e também pode ser publicado via Docker/Coolify.

## Comece Aqui

- Uso local: [`docs/USO-LOCAL.md`](docs/USO-LOCAL.md)
- Deploy em servidor/Coolify: [`docs/DEPLOY.md`](docs/DEPLOY.md)
- Guia antigo em português: [`LEIA-ME.md`](LEIA-ME.md)
- Guia antigo específico do Coolify: [`DEPLOY-COOLIFY.md`](DEPLOY-COOLIFY.md)

## Estrutura

```text
.
|-- app.py                  # aplicação principal, backend e interface embutida
|-- qrcode.min.js           # biblioteca local para QR Code
|-- assets/                 # logos e arquivos estáticos da interface
|-- Dockerfile              # imagem usada no deploy
|-- .dockerignore           # exclui arquivos locais do build Docker
|-- .gitignore              # exclui dados sensíveis e arquivos gerados do Git
|-- Iniciar-Windows.bat     # atalho local para Windows
|-- iniciar-linux.sh        # atalho local para Linux
|-- iniciar-mac.command     # atalho local para macOS
`-- docs/
    |-- USO-LOCAL.md        # instruções para rodar no computador
    `-- DEPLOY.md           # instruções para publicar em servidor/Coolify
```

## Dados e Segurança

- As chaves e logins ficam em `config.json`.
- Backups automáticos ficam em `backups/`.
- `config.json`, `backups/`, `__pycache__/` e `.claude/` não entram no Git.
- No deploy público, use sempre HTTPS e defina `IA_MONITOR_PASSWORD`.
- O servidor tem rate limit por IP ligado por padrão para reduzir abuso nas rotas do painel.

## Desenvolvimento

O app usa somente a biblioteca padrão do Python. Para validar rapidamente:

```bash
python -m py_compile app.py
python app.py
```

Depois acesse `http://127.0.0.1:8765`.
