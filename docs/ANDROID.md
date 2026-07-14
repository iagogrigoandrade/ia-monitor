# App Android

O projeto em [`android-app/`](../android-app/) e um app Android nativo. Ele abre o painel no `WebView` e inclui um widget de tela inicial com botao **Atualizar**.

## Antes de instalar

O celular precisa conseguir acessar o painel:

- Deploy: configure a URL HTTPS do Coolify, por exemplo `https://monitor.seudominio.com`.
- Rede local: inicie o painel escutando na rede local e use o IP do computador, por exemplo `http://192.168.1.50:8765`. `127.0.0.1:8765` aponta para o proprio celular e nao funciona para acessar o computador.
- Use HTTPS no deploy publico e mantenha `IA_MONITOR_PASSWORD` configurada.

## Gerar e instalar o APK

1. Instale o Android Studio e abra a pasta `android-app` como projeto.
2. Aguarde a sincronizacao do Gradle. O projeto usa Android SDK 35 e Java 17, ambos disponiveis pelo Android Studio.
3. Selecione `Build` > `Build Bundle(s) / APK(s)` > `Build APK(s)`.
4. Instale o arquivo gerado em `android-app/app/build/outputs/apk/debug/app-debug.apk` no celular. Autorize a instalacao de apps dessa origem caso o Android solicite.

## Configurar o app

1. Abra `Monitor de IA` no Android.
2. Informe a URL do painel. Se ele tiver senha, informe tambem usuario e senha.
3. A senha fica protegida no armazenamento criptografado do Android. O widget usa a mesma configuracao.

## Adicionar o widget

1. No app, abra o menu e toque em `Adicionar widget`; confirme o pedido do Android quando o launcher oferecer suporte.
2. Como alternativa, mantenha pressionada uma area vazia da tela inicial, escolha `Widgets` e arraste `Monitor de IA` para a tela.
3. Toque em **Atualizar** no widget para consultar o painel imediatamente. Ele chama `GET /api/status?force=1`, respeitando a protecao contra cliques repetidos ja existente no servidor.

O widget exibe todas as contas em cards rolaveis, com as metricas e barras de uso. Redimensione-o verticalmente para ver mais cards de uma vez. Toque fora do botao para abrir o app.
