# Monitor IA no Amazfit Active 2 (Round 44 mm)

Mini app de Zepp OS que mostra no relógio os limites e créditos do painel
**Monitor de IA** (Claude 5h/semanal, Codex, DeepSeek, OpenRouter).

Como funciona: o relógio pede os dados ao app **Zepp** do celular (via Bluetooth),
e o app Zepp busca `/api/status` do seu painel pela internet. Ou seja: o celular
precisa estar por perto e conseguir acessar o painel.

```text
Relógio (este app)  --Bluetooth-->  App Zepp no celular  --internet-->  Painel Monitor de IA
```

## 1. Pré-requisitos

- **Node.js 16+** no computador (https://nodejs.org)
- Celular com o app **Zepp** logado na mesma conta do relógio
- O painel acessível pelo celular:
  - **Servidor/Coolify (recomendado)**: use a URL pública HTTPS e defina `IA_MONITOR_PASSWORD`.
  - **Local (mesma rede Wi-Fi)**: rode o painel com `IA_MONITOR_HOST=0.0.0.0` e use
    `http://IP-DO-PC:8765` (ex.: `http://192.168.0.10:8765`). Sem `0.0.0.0` o painel
    só aceita o próprio computador.

## 2. Instalar as ferramentas e compilar

```bash
npm i -g @zeppos/zeus-cli
cd watch-app
npm install
zeus login        # entre com a MESMA conta Zepp do celular
zeus preview      # compila e mostra um QR Code
```

## 3. Ativar o Modo Desenvolvedor no app Zepp

1. Abra o app **Zepp** → aba **Perfil** → **Configurações** → **Sobre**.
2. Toque **7 vezes** no logotipo do Zepp → aparece "Modo do desenvolvedor".
3. Volte em **Configurações** → **Modo do desenvolvedor** → ative.
4. Com o relógio conectado, toque em **Escanear** (ícone de QR) e leia o QR Code
   do `zeus preview`. O app instala no relógio.

## 4. Configurar o endereço do painel

1. No app Zepp: **Perfil** → seu **Active 2** → **Aplicativos** (mini programas)
   → **Monitor IA** → ícone de configurações.
2. Preencha:
   - **Endereço do painel**: `https://seu-servidor` ou `http://192.168.0.10:8765`
   - **Usuário**: `admin` (padrão, ou o valor de `IA_MONITOR_USER`)
   - **Senha**: o valor de `IA_MONITOR_PASSWORD` (vazio se o painel não tem senha)
3. No relógio, abra o app **Monitor IA** (lista de apps). Ele atualiza sozinho a
   cada 60 s; o botão **Atualizar** força a consulta na hora.

## Testar sem o relógio (simulador)

```bash
zeus dev
```

Abre o simulador do Zepp OS no computador (instale via Zepp Console se pedido).

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "Configure o endereço do painel" | Configurações do mini app vazias no app Zepp |
| "Sem conexão com o app Zepp" | Celular longe/Bluetooth desligado, ou app Zepp fechado |
| "Falha ao conectar no painel" | URL errada ou painel não acessível pelo celular (teste a URL no navegador do celular) |
| "Senha do painel incorreta" | Usuário/senha diferentes de `IA_MONITOR_USER`/`IA_MONITOR_PASSWORD` |
| HTTP em rede local não conecta | Firewall do Windows bloqueando a porta 8765, ou painel sem `IA_MONITOR_HOST=0.0.0.0` |
| `Cannot find module 'zeppos-app-utils'` | Bug do zeus-cli no Node 24. Já está contornado pelo `_moduleAliases` no `package.json` — rode o zeus sempre de dentro da pasta `watch-app` (ou use `./node_modules/.bin/zeus`) |
