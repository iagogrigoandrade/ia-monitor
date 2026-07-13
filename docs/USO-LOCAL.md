# Uso Local

Este guia mostra como rodar o Monitor de IA no seu próprio computador.

## Requisitos

- Python 3 instalado.
- Navegador atualizado.
- Para login do Codex pelo painel: Codex CLI instalado quando for usar login local/QR do Codex.

Para instalar o Codex CLI, se necessário:

```bash
npm install -g @openai/codex
```

DeepSeek e OpenRouter não precisam de CLI: basta colar a API key no painel.

## Como Abrir

### Windows

Dê duplo clique em:

```text
Iniciar-Windows.bat
```

Ou rode no terminal dentro da pasta do projeto:

```powershell
py app.py
```

Se `py` não existir:

```powershell
python app.py
```

### macOS

Dê duplo clique em:

```text
iniciar-mac.command
```

Ou rode no Terminal:

```bash
python3 app.py
```

### Linux

Rode:

```bash
bash iniciar-linux.sh
```

Ou diretamente:

```bash
python3 app.py
```

## Endereço do Painel

Ao iniciar, o navegador deve abrir sozinho. Se não abrir, acesse:

```text
http://127.0.0.1:8765
```

Para fechar, encerre o terminal/janela onde o app está rodando.

## Limites Flutuantes no Windows

Depois de abrir o painel e fazer login, clique em `Flutuante`, ao lado do seletor de tema. No Chrome ou Microsoft Edge atualizados, os limites abrem em uma janela que fica visível sobre os outros programas. No Opera e em navegadores sem essa janela, o painel usa o Picture-in-Picture de vídeo com os limites desenhados localmente. Mantenha a aba principal do Monitor de IA aberta para continuar recebendo as atualizações.

## Adicionar Contas

Clique em `Adicionar conta` no painel.

### DeepSeek

1. Pegue a chave em `https://platform.deepseek.com/api_keys`.
2. Escolha `DeepSeek`.
3. Cole a API key.
4. Salve.

### OpenRouter

1. Pegue a chave em `https://openrouter.ai/settings/keys`.
2. Escolha `OpenRouter`.
3. Cole a API key.
4. Salve.

### Claude

1. Escolha `Claude`.
2. Dê um nome para a conta.
3. Use `Neste PC` ou `No celular (QR)`.
4. Faça login no site oficial.
5. Se o site mostrar um código, cole no painel e clique em `Concluir login`.

### Codex / ChatGPT

1. Escolha `Codex / ChatGPT`.
2. Dê um nome para a conta.
3. Use `Neste PC` ou `No celular (QR)`.
4. Faça login no fluxo oficial do Codex.
5. Aguarde o painel capturar o login.

Se o painel disser que não encontrou `codex`, instale o Codex CLI:

```bash
npm install -g @openai/codex
```

## Onde os Dados Ficam

Na execução local, os dados ficam na própria pasta do projeto:

- `config.json`: contas, API keys e tokens.
- `backups/`: cópias automáticas do `config.json`.

Esses arquivos são ignorados pelo Git e não devem ser enviados para o GitHub.

## Variáveis Opcionais

Você pode mudar o comportamento sem editar o código:

| Variável | Uso |
|---|---|
| `IA_MONITOR_PORT` | Porta HTTP. Padrão: `8765` |
| `IA_MONITOR_HOST` | Endereço de escuta. Padrão local: `127.0.0.1` |
| `IA_MONITOR_CONFIG` | Caminho alternativo do `config.json` |
| `IA_MONITOR_USER` | Usuário para autenticação quando houver senha |
| `IA_MONITOR_PASSWORD` | Ativa senha no painel |

Exemplo no Windows PowerShell:

```powershell
$env:IA_MONITOR_PORT="9000"
python app.py
```

Exemplo no Linux/macOS:

```bash
IA_MONITOR_PORT=9000 python3 app.py
```

## Problemas Comuns

- `Python não foi encontrado`: instale Python 3 e marque `Add Python to PATH` no Windows.
- `Login do Codex expirado`: remova a conta Codex no painel e adicione novamente.
- `Login do Claude expirado`: remova a conta Claude e faça login de novo.
- O navegador não abriu: entre manualmente em `http://127.0.0.1:8765`.
- Limites do Codex não aparecem: remova a conta Codex e adicione novamente para gravar o login atualizado.
- Serviço mostra bloqueio temporário: aguarde. O painel usa backoff automático.
