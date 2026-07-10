import { BasePage } from '@zeppos/zml/base-page'
import { createWidget, deleteWidget, widget, align, text_style, prop } from '@zos/ui'
import { setScrollMode, SCROLL_MODE_FREE } from '@zos/page'
import { setInterval, clearInterval } from '@zos/timer'
import { writeFileSync } from '@zos/fs'

// Tela do Active 2 Round: 466x466 (designWidth = 466, sem conversao px)
const W = 466
const MARGIN_X = 56
const CW = W - MARGIN_X * 2

const C_TITLE = 0x35d07f
const C_TEXT = 0xffffff
const C_MUTED = 0x8fa3b0
const C_ERR = 0xff5a5a
const C_BAR_BG = 0x1e2732
const C_BTN = 0x123524
const C_BTN_PRESS = 0x1d5238

function pctColor(p) {
  if (p >= 85) return 0xff5a5a
  if (p >= 60) return 0xffb340
  return 0x35d07f
}

Page(
  BasePage({
    state: {
      widgets: [], // widgets dinamicos (recriados a cada atualizacao)
      timer: null,
      loading: false,
    },

    onInit() {
      setScrollMode({ mode: SCROLL_MODE_FREE })
    },

    build() {
      // Titulo e linha de status ficam fixos (nao entram na lista dinamica)
      createWidget(widget.TEXT, {
        x: 0,
        y: 30,
        w: W,
        h: 42,
        color: C_TITLE,
        text_size: 32,
        align_h: align.CENTER_H,
        text: 'Monitor IA',
      })
      this.msg = createWidget(widget.TEXT, {
        x: MARGIN_X,
        y: 74,
        w: CW,
        h: 34,
        color: C_MUTED,
        text_size: 22,
        align_h: align.CENTER_H,
        text: 'Carregando...',
      })

      this.load(false)
      this.state.timer = setInterval(() => this.load(false), 60000)
    },

    onDestroy() {
      if (this.state.timer) {
        clearInterval(this.state.timer)
        this.state.timer = null
      }
    },

    setMsg(text, color) {
      this.msg.setProperty(prop.MORE, { text, color: color || C_MUTED })
    },

    load(force) {
      if (this.state.loading) return
      this.state.loading = true
      this.setMsg(force ? 'Atualizando...' : 'Carregando...')
      this.request({ method: 'GET_STATUS', force: !!force }, { timeout: 30000 })
        .then((data) => {
          this.state.loading = false
          if (!data || data.error) {
            this.setMsg((data && data.error) || 'Resposta vazia do celular', C_ERR)
            return
          }
          this.setMsg('Atualizado ' + (data.updated_at || ''))
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
          this.setMsg('Sem conexao com o app Zepp', C_ERR)
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
      let y = 118
      const accounts = data.accounts || []

      if (!accounts.length) {
        this.add(widget.TEXT, {
          x: MARGIN_X,
          y,
          w: CW,
          h: 80,
          color: C_MUTED,
          text_size: 24,
          align_h: align.CENTER_H,
          text_style: text_style.WRAP,
          text: 'Nenhuma conta no painel',
        })
        y += 100
      }

      accounts.forEach((acc) => {
        // Nome da conta (ex: "Claude" ou o apelido dado no painel)
        this.add(widget.TEXT, {
          x: MARGIN_X,
          y,
          w: CW,
          h: 36,
          color: C_TEXT,
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
            color: C_ERR,
            text_size: 20,
            align_h: align.LEFT,
            text_style: text_style.WRAP,
            text: acc.error,
          })
          y += 92
          return
        }

        const metrics = acc.metrics || []
        metrics.forEach((m) => {
          if (typeof m.percent === 'number') {
            // Linha "Limite 5h                       34%"
            this.add(widget.TEXT, {
              x: MARGIN_X,
              y,
              w: CW - 90,
              h: 28,
              color: C_MUTED,
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
              color: pctColor(m.percent),
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
              color: C_BAR_BG,
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
              color: pctColor(m.percent),
            })
            y += 22
            if (m.reset) {
              this.add(widget.TEXT, {
                x: MARGIN_X,
                y,
                w: CW,
                h: 26,
                color: C_MUTED,
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
              color: C_TEXT,
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
            color: 0xffb340,
            text_size: 18,
            align_h: align.LEFT,
            text_style: text_style.WRAP,
            text: 'Valor antigo: ' + acc.stale,
          })
          y += 58
        }

        y += 22 // espaco entre contas
      })

      // Botao de atualizar no fim da lista
      this.add(widget.BUTTON, {
        x: MARGIN_X,
        y,
        w: CW,
        h: 64,
        radius: 32,
        text_size: 26,
        color: C_TITLE,
        normal_color: C_BTN,
        press_color: C_BTN_PRESS,
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
        color: 0x000000,
      })
    },
  })
)
