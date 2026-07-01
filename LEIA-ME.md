# 🧠 Monitor de IA

Painel para acompanhar, em um só lugar:

- **Claude** — limite de 5 horas e limite semanal
- **Codex / ChatGPT** — limite de 5 horas e limite semanal
- **DeepSeek** — saldo
- **OpenRouter** — créditos

Funciona em **Windows, Linux e Mac**. Suporta **várias contas** do mesmo serviço.
Tudo roda na sua máquina — nada é enviado para lugar nenhum além do próprio serviço de cada IA.

---

## ▶️ Como abrir

- **Windows:** dê **duplo clique** em `Iniciar-Windows.bat`
- **Mac:** dê **duplo clique** em `iniciar-mac.command`
- **Linux:** dê duplo clique em `iniciar-linux.sh` (ou rode `bash iniciar-linux.sh`)

Vai abrir sozinho no navegador. Se não abrir, acesse: **http://127.0.0.1:8765**

Para fechar: feche a janela preta (terminal) que apareceu.

> **Precisa ter Python instalado.** No Mac e Linux já vem. No Windows, se aparecer um aviso
> pedindo Python, instale em https://www.python.org/downloads/ e marque *"Add Python to PATH"*.

---

## ➕ Como adicionar suas contas

Clique em **"+ Adicionar conta"** no painel e escolha o serviço.

### DeepSeek e OpenRouter (é só colar a chave)
1. Pegue sua API key:
   - DeepSeek: https://platform.deepseek.com/api_keys
   - OpenRouter: https://openrouter.ai/settings/keys
2. Cole no campo e salve.
3. Para **mais de uma conta**, repita adicionando outra chave.

### Claude e Codex (login pelo próprio painel — sem terminal)
1. Clique **+ Adicionar conta**, escolha **Claude** ou **Codex**, dê um nome.
2. Escolha como entrar:
   - **💻 Neste PC** — abre a página de login aqui no computador (uma janela só).
     - *Codex:* faça login e pronto, ele detecta sozinho.
     - *Claude:* o site mostra um **código** → cole no campo e clique **Concluir login**.
   - **📱 No celular (QR)** — aparece um **QR Code**. Escaneie com a câmera do celular:
     - *Codex:* faça login no celular e **digite o código** que aparece no painel; conclui sozinho.
     - *Claude:* faça login no celular, ele mostra um **código** → digite no painel e **Concluir**.
3. Também há **"Usar login atual"**: aproveita a conta já logada no computador (mais rápido).

**Para monitorar 2 contas diferentes (ex: 2 do Codex):**
1. Adicione a **Conta 1** (nome: "Codex Conta 1").
2. Abra **+ Adicionar conta** de novo e entre na **outra** conta (nome: "Codex Conta 2").
   Se entrar direto na conta errada, use "trocar de conta" na página de login.

Você pode adicionar **quantas contas quiser**, misturando serviços. O painel mostra
todas lado a lado, cada uma se atualizando sozinha. (Faça **um login por vez**.)

---

## ⚙️ Detalhes

- As chaves e logins ficam salvos só no arquivo `config.json`, dentro desta pasta, na sua máquina.
- Toda vez que algo muda, é guardada uma cópia de segurança na pasta `backups/` (últimas 20).
  Se perder uma conta, dá para recuperar a partir de lá.
- Cada card mostra **seu próprio** intervalo de atualização ("auto a cada Xs"). Esse intervalo é
  **definido automaticamente** conforme o limite de cada serviço (você não precisa configurar nada).
- **Reordene os cards arrastando** (segure e arraste pela alça no canto do card). A ordem fica salva.
- As barras ficam **verdes** (tranquilo), **amarelas** (acima de 70%) e **vermelhas** (acima de 90%).

### Tem limite nessas consultas?
Essas consultas só **leem** seu uso/saldo — **não gastam** mensagens nem tokens.
O painel respeita um intervalo mínimo **por provedor**, para nunca passar do limite de cada um:

| Provedor | Intervalo automático | Motivo |
|---|---|---|
| DeepSeek | 60s | endpoint leve, sem limite fixo |
| OpenRouter | 60s | teto de ~20 consultas/min |
| Claude | 120s | endpoint propenso a bloqueio por excesso |
| Codex | 300s | protegido por firewall que bloqueia rajadas |

Esses valores são **definidos pelo sistema** (você não edita, para não quebrar). No automático,
cada serviço é consultado no seu próprio ritmo, com folga.
**Quando você clica em "Atualizar agora", ele consulta TODOS na hora.**
Se um serviço ficar bloqueado (rajada), o painel **recua sozinho** (espera cada vez mais) e mostra
o **último valor** com o aviso "atualizando…" — voltando ao normal quando o serviço libera.

## ❓ Problemas comuns

- **"Login do Codex/Claude expirado"** → remova a conta e adicione de novo com **🌐 Entrar com o navegador**.
- **Não abre o navegador** → acesse manualmente `http://127.0.0.1:8765`.
- **Nada aparece** → confira se as chaves estão corretas e se você tem saldo/limite ativo.
