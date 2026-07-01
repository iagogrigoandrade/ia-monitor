# 🚀 Colocar o Monitor de IA no Coolify (passo a passo)

Guia para quem **não é programador**. Objetivo: acessar seu painel de qualquer
celular/computador, protegido por senha e com HTTPS.

> ⚠️ **Segurança:** este painel guarda suas chaves e logins. NUNCA deixe sem senha
> na internet. Os passos abaixo já configuram uma senha (variável `IA_MONITOR_PASSWORD`).
> Suas chaves **nunca** aparecem no navegador — o painel só mostra as barras de uso.

---

## Parte 1 — Colocar o projeto no GitHub (sem instalar nada)

O Coolify precisa buscar o projeto de algum lugar. O jeito mais fácil é o GitHub,
usando só o site (arrastar e soltar os arquivos).

1. Crie uma conta grátis em https://github.com (se ainda não tiver).
2. Clique em **New repository** (novo repositório).
   - **Name:** `ia-monitor`
   - Marque **Private** (privado — importante).
   - Clique **Create repository**.
3. Na página do repositório, clique em **uploading an existing file**
   (ou **Add file → Upload files**).
4. Arraste para lá **todos** os arquivos desta pasta, principalmente:
   - `app.py`
   - `qrcode.min.js`
   - `Dockerfile`
   - `.dockerignore`
   > NÃO envie `config.json` nem a pasta `backups/` (têm suas chaves). O `.dockerignore`
   > já ignora, mas por segurança não faça upload deles.
5. Clique em **Commit changes** (salvar).

---

## Parte 2 — Criar o app no Coolify

1. Entre no seu Coolify (o da Hostinger).
2. Escolha um **Project** (ou crie um) → **+ New Resource**.
3. Escolha **Private Repository (with GitHub App)** ou **Public/Private Repository**
   e conecte sua conta do GitHub. Selecione o repositório `ia-monitor`.
4. Em **Build Pack**, escolha **Dockerfile** (o Coolify costuma detectar sozinho).
5. Em **Port / Ports Exposes**, coloque **8765**.
6. Clique em **Continue / Save** (ainda NÃO faça o deploy — faltam 2 ajustes abaixo).

---

## Parte 3 — Definir a senha (variáveis de ambiente)

No app criado, abra a aba **Environment Variables** e adicione:

| Nome (Key)             | Valor (Value)                          |
|------------------------|----------------------------------------|
| `IA_MONITOR_PASSWORD`  | *uma senha forte sua* (ex: `Gold@2026#painel`) |
| `IA_MONITOR_USER`      | `admin` (ou o usuário que quiser)      |

Salve. **Guarde essa senha** — é ela que você vai digitar ao abrir o painel.

> As variáveis `IA_MONITOR_HOST`, `IA_MONITOR_PORT` e `IA_MONITOR_CONFIG` já vêm
> definidas no `Dockerfile` — não precisa mexer.

---

## Parte 4 — Guardar suas contas para sempre (volume persistente)

Sem isso, ao reiniciar o servidor você perde as contas cadastradas.

1. No app, abra a aba **Storages** (ou **Persistent Storage**).
2. Clique em **+ Add** e configure:
   - **Name:** `dados`
   - **Destination Path (dentro do container):** `/data`
3. Salve.

---

## Parte 5 — Domínio e HTTPS

1. Na aba **General / Domains**, coloque um endereço, por exemplo:
   `https://monitor.seudominio.com` (ou use o domínio grátis que o Coolify oferece).
2. O Coolify gera o **HTTPS (cadeado)** automaticamente. Isso é importante:
   com HTTPS sua senha viaja protegida.
3. Se usar domínio próprio, aponte o DNS dele para o IP do servidor (registro **A**).

---

## Parte 6 — Publicar

1. Clique em **Deploy**.
2. Aguarde o log terminar (fica verde / "running").
3. Abra o endereço no navegador. Vai pedir **usuário e senha** — use os que você
   definiu na Parte 3. Pronto! 🎉

---

## Dúvidas comuns

- **Pediu senha e não entra:** confira `IA_MONITOR_USER` e `IA_MONITOR_PASSWORD`
  (maiúsculas/minúsculas contam). Depois de mudar, clique **Redeploy**.
- **Aparece "unhealthy":** o app responde em `/healthz` sem senha; se o Coolify
  insistir, desative o **Health Check** nas configurações do app.
- **Perdi minhas contas ao reiniciar:** faltou o volume `/data` (Parte 4).
- **Login do Claude/Codex:** ao adicionar essas contas no painel, use a opção
  **📱 QR / celular** — é a que funciona bem num servidor remoto. DeepSeek e
  OpenRouter é só colar a chave normalmente.
- **Quero trocar a senha:** mude a variável `IA_MONITOR_PASSWORD` e clique Redeploy.
</content>
