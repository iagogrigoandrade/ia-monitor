# Deploy

Este guia cobre o deploy do Monitor de IA em servidor, especialmente via Coolify.

## Antes de Publicar

Checklist obrigatório:

- Repositório GitHub conectado ao Coolify.
- `Dockerfile` presente na raiz.
- Porta exposta: `8765`.
- HTTPS ativo no domínio.
- Senha configurada com `IA_MONITOR_PASSWORD`.
- Volume persistente montado em `/data`.

Nunca envie estes arquivos para o GitHub:

- `config.json`
- `backups/`
- `__pycache__/`
- `.claude/`

Eles já estão cobertos pelo `.gitignore` ou `.dockerignore`, mas confira antes de subir qualquer coisa.

## Deploy no Coolify

### 1. Criar o App

1. Abra o Coolify.
2. Entre em um projeto.
3. Clique em `+ New Resource`.
4. Escolha repositório GitHub.
5. Selecione o repositório `ia-monitor`.
6. Use build pack `Dockerfile`.
7. Configure a porta como `8765`.

### 2. Variáveis de Ambiente

No Coolify, abra `Environment Variables` e configure:

| Variável | Valor recomendado |
|---|---|
| `IA_MONITOR_PASSWORD` | uma senha forte |
| `IA_MONITOR_USER` | `admin` ou outro usuário |

O `Dockerfile` já define:

| Variável | Valor |
|---|---|
| `IA_MONITOR_CONFIG` | `/data/config.json` |
| `IA_MONITOR_HOST` | `0.0.0.0` |
| `IA_MONITOR_PORT` | `8765` |

Você normalmente não precisa alterar essas três.

### 3. Volume Persistente

Sem volume, você perde as contas cadastradas ao reiniciar/recriar o container.

No Coolify, configure um storage persistente:

| Campo | Valor |
|---|---|
| Name | `dados` |
| Destination Path | `/data` |

O app gravará em:

- `/data/config.json`
- `/data/backups/`

### 4. Domínio e HTTPS

1. Configure um domínio no Coolify, por exemplo `https://monitor.seudominio.com`.
2. Ative/aguarde o HTTPS automático.
3. Se usar domínio próprio, aponte o DNS para o IP do servidor.

Não publique este painel sem HTTPS. A autenticação básica depende do HTTPS para proteger usuário e senha em trânsito.

### 5. Health Check

O app responde sem senha em:

```text
/healthz
```

Resposta esperada:

```json
{"ok": true}
```

Se o Coolify reclamar de health check, configure o caminho `/healthz` ou desative o health check do app.

### 6. Publicar

1. Clique em `Deploy`.
2. Aguarde o build terminar.
3. Abra o domínio configurado.
4. Entre com `IA_MONITOR_USER` e `IA_MONITOR_PASSWORD`.

## Atualizar o Deploy

Quando houver mudanças no GitHub:

1. Faça push para a branch `main`.
2. No Coolify, clique em `Redeploy`, se ele não fizer automaticamente.
3. O volume `/data` preserva suas contas.

## Login de Contas em Servidor

### DeepSeek e OpenRouter

Funcionam normalmente: cole a API key no painel.

### Claude e Codex

Em servidor, prefira `No celular (QR)`:

1. Abra o painel pelo domínio HTTPS.
2. Clique em `Adicionar conta`.
3. Escolha Claude ou Codex.
4. Use `No celular (QR)`.
5. Conclua o fluxo oficial.

## Segurança

- Use senha forte em `IA_MONITOR_PASSWORD`.
- Use HTTPS.
- Não compartilhe o link e a senha do painel.
- Não envie `config.json` para o GitHub.
- Faça backup do volume `/data` se o servidor for importante.

Mesmo com acesso ao painel, as API keys e tokens não são mostrados pela interface. Eles ficam no servidor, no arquivo `config.json`.

## Problemas Comuns

- Pede senha e não entra: confira `IA_MONITOR_USER` e `IA_MONITOR_PASSWORD` e faça redeploy.
- Perdeu contas após redeploy: faltou volume persistente em `/data`.
- Codex não mostra limite: remova a conta Codex e adicione novamente.
- Claude/Codex não conclui login: use a opção `No celular (QR)` no servidor.
- App não sobe: confira se o build pack é `Dockerfile` e se a porta é `8765`.
