import { BasePage } from '@zeppos/zml/base-page'
import { createWidget, deleteWidget, widget, align, text_style, prop } from '@zos/ui'
import { setScrollMode, SCROLL_MODE_FREE } from '@zos/page'
import { setInterval, clearInterval } from '@zos/timer'
import { writeFileSync, readFileSync } from '@zos/fs'
import { set as setAlarm, getAllAlarms, REPEAT_MINUTE } from '@zos/alarm'
import { onGesture, offGesture, GESTURE_LEFT } from '@zos/interaction'

// Tela do Active 2 Round: 466x466 (designWidth = 466, sem conversao px)
const W = 466
const MARGIN_X = 56
const CW = W - MARGIN_X * 2
const CARD_X = 36
const CARD_W = W - CARD_X * 2

// Temas claro/escuro. Salvo em theme.json para o widget usar o mesmo.
const THEMES = {
  dark: {
    bg: 0x000000,
    card: 0x141c26,
    text: 0xffffff,
    muted: 0x8fa3b0,
    accent: 0x35d07f,
    barBg: 0x2a3644,
    btn: 0x123524,
    btnPress: 0x1d5238,
    err: 0xff5a5a,
    warn: 0xffb340,
    pctHigh: 0xff5a5a,
    pctMid: 0xffb340,
    pctLow: 0x35d07f,
  },
  light: {
    bg: 0xf2f5f3,
    card: 0xffffff,
    text: 0x111a15,
    muted: 0x5b6e63,
    accent: 0x0f8a52,
    barBg: 0xdde5df,
    btn: 0xcdeeda,
    btnPress: 0xaee2c4,
    err: 0xd93636,
    warn: 0xb87400,
    pctHigh: 0xd93636,
    pctMid: 0xb87400,
    pctLow: 0x0f8a52,
  },
}

function pctColor(T, p) {
  if (p >= 85) return T.pctHigh
  if (p >= 60) return T.pctMid
  return T.pctLow
}

function loadDark() {
  try {
    const raw = readFileSync({ path: 'theme.json', options: { encoding: 'utf8' } })
    if (raw) return !!JSON.parse(raw).dark
  } catch (e) {}
  return true // padrao: escuro
}

Page(
  BasePage({
    state: {
      widgets: [], // widgets dinamicos (recriados a cada atualizacao)
      fixed: [], // fundo, titulo e linha de status
      timer: null,
      loading: false,
      dark: true,
      lastData: null,
      msgText: 'Carregando...',
      msgColor: null,
    },

    T() {
      return this.state.dark ? THEMES.dark : THEMES.light
    },

    onInit() {
      setScrollMode({ mode: SCROLL_MODE_FREE })
      this.state.dark = loadDark()
    },

    build() {
      this.buildFixed()
      this.load(false)
      this.state.timer = setInterval(() => this.load(false), 60000)
      this.ensureAlarm()

      // Arrastar para a ESQUERDA alterna claro/escuro
      // (direita continua sendo o gesto do sistema para sair)
      onGesture({
        callback: (ev) => {
          if (ev === GESTURE_LEFT) {
            this.toggleTheme()
            return true
          }
          return false
        },
      })
    },

    // Fundo, titulo e linha de status (recriados ao trocar de tema)
    buildFixed() {
      const T = this.T()
      this.bg = createWidget(widget.FILL_RECT, {
        x: 0,
        y: 0,
        w: W,
        h: W,
        color: T.bg,
      })
      this.state.fixed.push(this.bg)

      const title = createWidget(widget.TEXT, {
        x: 0,
        y: 30,
        w: W,
        h: 42,
        color: T.accent,
        text_size: 32,
        align_h: align.CENTER_H,
        text: 'Monitor IA',
      })
      this.state.fixed.push(title)

      this.msg = createWidget(widget.TEXT, {
        x: MARGIN_X,
        y: 74,
        w: CW,
        h: 34,
        color: this.state.msgColor || T.muted,
        text_size: 22,
        align_h: align.CENTER_H,
        text: this.state.msgText,
      })
      this.state.fixed.push(this.msg)
    },

    toggleTheme() {
      this.state.dark = !this.state.dark
      try {
        writeFileSync({
          path: 'theme.json',
          data: JSON.stringify({ dark: this.state.dark }),
          options: { encoding: 'utf8' },
        })
      } catch (e) {}
      // Recria tudo com as cores novas
      this.state.msgColor = null
      this.clear()
      this.state.fixed.forEach((w) => deleteWidget(w))
      this.state.fixed = []
      this.buildFixed()
      if (this.state.lastData) this.render(this.state.lastData)
    },

    // Agenda o vigia em segundo plano (a cada 15 min) uma unica vez.
    // O alarme sobrevive a reinicio do relogio (store: true).
    ensureAlarm() {
      try {
        const existing = getAllAlarms()
        if (existing && existing.length) return
        setAlarm({
          url: 'app-service/index',
          delay: 900,
          repeat_type: REPEAT_MINUTE,
          repeat_period: 15,
          store: true,
        })
      } catch (e) {}
    },

    onDestroy() {
      try {
        offGesture()
      } catch (e) {}
      if (this.state.timer) {
        clearInterval(this.state.timer)
        this.state.timer = null
      }
    },

    setMsg(text, color) {
      this.state.msgText = text
      this.state.msgColor = color || null
      this.msg.setProperty(prop.MORE, { text, color: color || this.T().muted })
    },

    load(force) {
      if (this.state.loading) return
      this.state.loading = true
      this.setMsg(force ? 'Atualizando...' : 'Carregando...')
      this.request({ method: 'GET_STATUS', force: !!force }, { timeout: 30000 })
        .then((data) => {
          this.state.loading = false
          if (!data || data.error) {
            this.setMsg((data && data.error) || 'Resposta vazia do celular', this.T().err)
            return
          }
          this.setMsg('Atualizado ' + (data.updated_at || ''))
          this.state.lastData = data
          this.render(data)
          // Guarda os dados em arquivo para o widget (cartao) mostrar.
          // Arquivo, e nao localStorage: o widget roda em outro contexto
          // e nao enxerga o localStorage da pagina.
          try {
            writeFileSync({
              path: 'last_status.json',
              data: JSON.stringify(data),
              options: { encoding: 'utf8' },
            })
          } catch (e) {}
        })
        .catch(() => {
          this.state.loading = false
          this.setMsg('Sem conexao com o app Zepp', this.T().err)
        })
    },

    clear() {
      this.state.widgets.forEach((w) => deleteWidget(w))
      this.state.widgets = []
    },

    add(type, opts) {
      const w = createWidget(type, opts)
      this.state.widgets.push(w)
      return w
    },

    render(data) {
      this.clear()
      const T = this.T()
      let y = 118
      const accounts = data.accounts || []

      if (!accounts.length) {
        this.add(widget.TEXT, {
          x: MARGIN_X,
          y,
          w: CW,
          h: 80,
          color: T.muted,
          text_size: 24,
          align_h: align.CENTER_H,
          text_style: text_style.WRAP,
          text: 'Nenhuma conta no painel',
        })
        y += 100
      }

      accounts.forEach((acc) => {
        // Bloquinho (cartao) da conta: fundo criado antes do conteudo,
        // altura ajustada no final quando ja se sabe o tamanho
        const cardTop = y
        const card = this.add(widget.FILL_RECT, {
          x: CARD_X,
          y: cardTop,
          w: CARD_W,
          h: 100,
          radius: 20,
          color: T.card,
        })
        y += 18

        // Nome da conta (ex: "Claude" ou o apelido dado no painel)
        this.add(widget.TEXT, {
          x: MARGIN_X,
          y,
          w: CW,
          h: 36,
          color: T.text,
          text_size: 28,
          align_h: align.LEFT,
          text_style: text_style.ELLIPSIS,
          text: acc.label || acc.typeLabel || '?',
        })
        y += 40

        if (acc.error) {
          this.add(widget.TEXT, {
            x: MARGIN_X,
            y,
            w: CW,
            h: 84,
            color: T.err,
            text_size: 20,
            align_h: align.LEFT,
            text_style: text_style.WRAP,
            text: acc.error,
          })
          y += 88
        } else {
          const metrics = acc.metrics || []
          metrics.forEach((m) => {
            if (typeof m.percent === 'number') {
              // Linha "Limite 5h                       34%"
              this.add(widget.TEXT, {
                x: MARGIN_X,
                y,
                w: CW - 90,
                h: 28,
                color: T.muted,
                text_size: 22,
                align_h: align.LEFT,
                text_style: text_style.ELLIPSIS,
                text: m.label || '',
              })
              this.add(widget.TEXT, {
                x: MARGIN_X + CW - 90,
                y,
                w: 90,
                h: 28,
                color: pctColor(T, m.percent),
                text_size: 22,
                align_h: align.RIGHT,
                text: Math.round(m.percent) + '%',
              })
              y += 32
              // Barra de progresso
              this.add(widget.FILL_RECT, {
                x: MARGIN_X,
                y,
                w: CW,
                h: 14,
                radius: 7,
                color: T.barBg,
              })
              const fillW = Math.max(
                14,
                Math.round((Math.min(100, m.percent) / 100) * CW)
              )
              this.add(widget.FILL_RECT, {
                x: MARGIN_X,
                y,
                w: fillW,
                h: 14,
                radius: 7,
                color: pctColor(T, m.percent),
              })
              y += 22
              if (m.reset) {
                this.add(widget.TEXT, {
                  x: MARGIN_X,
                  y,
                  w: CW,
                  h: 26,
                  color: T.muted,
                  text_size: 18,
                  align_h: align.LEFT,
                  text_style: text_style.ELLIPSIS,
                  text: 'Zera: ' + m.reset,
                })
                y += 28
              }
              y += 6
            } else {
              // Metrica de valor (saldo/creditos)
              const val =
                m.value === null || m.value === undefined ? '-' : String(m.value)
              this.add(widget.TEXT, {
                x: MARGIN_X,
                y,
                w: CW,
                h: 30,
                color: T.text,
                text_size: 22,
                align_h: align.LEFT,
                text_style: text_style.ELLIPSIS,
                text: (m.label || '') + ': ' + val + (m.unit ? ' ' + m.unit : ''),
              })
              y += 36
            }
          })

          if (acc.stale) {
            this.add(widget.TEXT, {
              x: MARGIN_X,
              y,
              w: CW,
              h: 52,
              color: T.warn,
              text_size: 18,
              align_h: align.LEFT,
              text_style: text_style.WRAP,
              text: 'Valor antigo: ' + acc.stale,
            })
            y += 58
          }
        }

        y += 8 // respiro no fim do cartao
        card.setProperty(prop.MORE, {
          x: CARD_X,
          y: cardTop,
          w: CARD_W,
          h: y - cardTop,
        })
        y += 18 // espaco entre cartoes
      })

      y += 6

      // Botao de atualizar no fim da lista
      this.add(widget.BUTTON, {
        x: MARGIN_X,
        y,
        w: CW,
        h: 64,
        radius: 32,
        text_size: 26,
        color: T.accent,
        normal_color: T.btn,
        press_color: T.btnPress,
        text: 'Atualizar',
        click_func: () => this.load(true),
      })
      y += 84

      // Espaco extra no fim para a tela redonda nao cortar o botao
      this.add(widget.FILL_RECT, {
        x: 0,
        y,
        w: W,
        h: 60,
        color: T.bg,
      })
      y += 60

      // Estica o fundo para cobrir todo o conteudo rolavel
      this.bg.setProperty(prop.MORE, { x: 0, y: 0, w: W, h: Math.max(W, y) })
    },
  })
)
