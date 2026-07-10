import { readFileSync } from '@zos/fs'
import { createWidget, deleteWidget, widget, align, text_style, event } from '@zos/ui'
import { push } from '@zos/router'

// Widget (cartao ao deslizar na tela). Sem rolagem e sem Bluetooth aqui:
// mostra os ultimos dados salvos pelo app e, ao tocar, abre o app completo.
const W = 466
const MX = 64
const CW = W - MX * 2

const C_TITLE = 0x35d07f
const C_TEXT = 0xffffff
const C_MUTED = 0x8fa3b0
const C_ERR = 0xff5a5a
const C_BAR_BG = 0x1e2732

function pctColor(p) {
  if (p >= 85) return 0xff5a5a
  if (p >= 60) return 0xffb340
  return 0x35d07f
}

function openApp() {
  try {
    push({ url: 'page/index' })
  } catch (e) {}
}

// Resume de uma conta em uma linha: pega o limite mais cheio (pior caso)
// ou, se for conta de saldo, o primeiro valor.
function summarize(acc) {
  if (acc.error) return { text: 'erro', color: C_ERR, percent: null }
  const ms = acc.metrics || []
  let worst = null
  for (let i = 0; i < ms.length; i++) {
    const m = ms[i]
    if (typeof m.percent === 'number' && (!worst || m.percent > worst.percent)) {
      worst = m
    }
  }
  if (worst) {
    const l = String(worst.label || '').toLowerCase()
    const short = l.indexOf('sem') >= 0 ? 'sem ' : l.indexOf('5h') >= 0 ? '5h ' : ''
    return {
      text: short + Math.round(worst.percent) + '%',
      color: pctColor(worst.percent),
      percent: Math.min(100, worst.percent),
    }
  }
  const v = ms[0]
  if (v && v.value !== undefined && v.value !== null) {
    return {
      text: String(v.value) + (v.unit ? ' ' + v.unit : ''),
      color: C_TEXT,
      percent: null,
    }
  }
  return { text: '-', color: C_MUTED, percent: null }
}

SecondaryWidget({
  state: { widgets: [] },

  build() {
    try {
      this.render()
    } catch (e) {}
  },

  onResume() {
    try {
      this.render()
    } catch (e) {}
  },

  add(type, opts) {
    const w = createWidget(type, opts)
    // Qualquer toque no widget abre o app completo
    w.addEventListener(event.CLICK_UP, openApp)
    this.state.widgets.push(w)
    return w
  },

  render() {
    this.state.widgets.forEach((w) => deleteWidget(w))
    this.state.widgets = []

    // Fundo preto clicavel cobrindo tudo (BUTTON garante o toque no widget)
    this.add(widget.BUTTON, {
      x: 0,
      y: 0,
      w: W,
      h: W,
      radius: 0,
      text: '',
      normal_color: 0x000000,
      press_color: 0x0d1117,
      click_func: openApp,
    })

    this.add(widget.TEXT, {
      x: 0,
      y: 40,
      w: W,
      h: 38,
      color: C_TITLE,
      text_size: 28,
      align_h: align.CENTER_H,
      text: 'Monitor IA',
    })

    let data = null
    try {
      const raw = readFileSync({
        path: 'last_status.json',
        options: { encoding: 'utf8' },
      })
      if (raw) data = JSON.parse(raw)
    } catch (e) {}

    if (!data || !data.accounts || !data.accounts.length) {
      this.add(widget.TEXT, {
        x: MX,
        y: 150,
        w: CW,
        h: 160,
        color: C_MUTED,
        text_size: 24,
        align_h: align.CENTER_H,
        text_style: text_style.WRAP,
        text: 'Toque para abrir o app e carregar os dados',
      })
      return
    }

    let y = 92
    const accounts = data.accounts.slice(0, 4)
    accounts.forEach((acc) => {
      const s = summarize(acc)
      this.add(widget.TEXT, {
        x: MX,
        y,
        w: CW - 140,
        h: 30,
        color: C_TEXT,
        text_size: 24,
        align_h: align.LEFT,
        text_style: text_style.ELLIPSIS,
        text: acc.label || acc.typeLabel || '?',
      })
      this.add(widget.TEXT, {
        x: MX + CW - 140,
        y,
        w: 140,
        h: 30,
        color: s.color,
        text_size: 24,
        align_h: align.RIGHT,
        text_style: text_style.ELLIPSIS,
        text: s.text,
      })
      y += 32
      if (s.percent !== null) {
        this.add(widget.FILL_RECT, {
          x: MX,
          y,
          w: CW,
          h: 8,
          radius: 4,
          color: C_BAR_BG,
        })
        this.add(widget.FILL_RECT, {
          x: MX,
          y,
          w: Math.max(8, Math.round((s.percent / 100) * CW)),
          h: 8,
          radius: 4,
          color: pctColor(s.percent),
        })
        y += 14
      }
      y += 10
    })

    if (data.accounts.length > 4) {
      this.add(widget.TEXT, {
        x: MX,
        y,
        w: CW,
        h: 24,
        color: C_MUTED,
        text_size: 18,
        align_h: align.CENTER_H,
        text: '+' + (data.accounts.length - 4) + ' no app',
      })
      y += 26
    }

    this.add(widget.TEXT, {
      x: MX,
      y: 386,
      w: CW,
      h: 26,
      color: C_MUTED,
      text_size: 18,
      align_h: align.CENTER_H,
      text: 'Atualizado ' + (data.updated_at || '--') + ' · toque p/ abrir',
    })
  },
})
