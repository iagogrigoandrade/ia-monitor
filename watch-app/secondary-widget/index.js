import { readFileSync } from '@zos/fs'
import { createWidget, deleteWidget, widget, align, text_style } from '@zos/ui'
import { push } from '@zos/router'

// Widget (cartao ao deslizar na tela). Sem rolagem e sem Bluetooth aqui:
// mostra o limite principal de cada conta (5h; senao o primeiro com %,
// ex: semanal do Codex) e quanto falta para zerar.
// Apenas o rodape ("toque p/ abrir") abre o app completo.
const W = 466
const MX = 64
const CW = W - MX * 2

// Mesmos temas do app; o modo escolhido fica salvo em theme.json
const THEMES = {
  dark: {
    bg: 0x000000,
    press: 0x1e2732,
    text: 0xffffff,
    muted: 0x8fa3b0,
    accent: 0x35d07f,
    barBg: 0x1e2732,
    err: 0xff5a5a,
    pctHigh: 0xff5a5a,
    pctMid: 0xffb340,
    pctLow: 0x35d07f,
  },
  light: {
    bg: 0xd4d8d6, // cinza, um pouco menos claro (mesmo fundo do app)
    press: 0xbcc2bf,
    text: 0x111a15,
    muted: 0x50605a,
    accent: 0x0f8a52,
    barBg: 0xbcc2bf,
    err: 0xd93636,
    pctHigh: 0xd93636,
    pctMid: 0xb87400,
    pctLow: 0x0f8a52,
  },
}

function loadTheme() {
  try {
    const raw = readFileSync({ path: 'theme.json', options: { encoding: 'utf8' } })
    if (raw && !JSON.parse(raw).dark) return THEMES.light
  } catch (e) {}
  return THEMES.dark
}

function pctColor(T, p) {
  if (p >= 85) return T.pctHigh
  if (p >= 60) return T.pctMid
  return T.pctLow
}

function openApp() {
  try {
    push({ url: 'page/index' })
  } catch (e) {}
}

// Acha a metrica principal da conta: prefere o limite de 5h;
// se nao houver (ex: Codex hoje so manda o semanal), usa a
// primeira metrica que tenha porcentagem.
function findMain(acc) {
  const ms = acc.metrics || []
  for (let i = 0; i < ms.length; i++) {
    const l = String(ms[i].label || '').toLowerCase()
    if (l.indexOf('5h') >= 0 || l.indexOf('5 h') >= 0) return ms[i]
  }
  for (let i = 0; i < ms.length; i++) {
    if (typeof ms[i].percent === 'number') return ms[i]
  }
  return null
}

// Texto de quanto falta para o limite zerar, calculado na hora do render
function resetText(m) {
  if (m.resetTs) {
    const now = Math.floor(Date.now() / 1000)
    const s = m.resetTs - now
    if (s > 0) {
      const h = Math.floor(s / 3600)
      const min = Math.floor((s % 3600) / 60)
      if (h >= 24) {
        const d = Math.floor(h / 24)
        return 'Zera em ' + d + 'd ' + (h % 24) + 'h'
      }
      return h > 0 ? 'Zera em ' + h + 'h ' + min + 'min' : 'Zera em ' + min + 'min'
    }
    return 'Renovando...'
  }
  if (m.reset) return 'Zera: ' + m.reset
  return null
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
    this.state.widgets.push(w)
    return w
  },

  // Rodape clicavel: unica area que abre o app
  addFooter(T, label) {
    this.add(widget.BUTTON, {
      x: MX - 20,
      y: 366,
      w: CW + 40,
      h: 64,
      radius: 12,
      text: label,
      text_size: 18,
      color: T.muted,
      normal_color: T.bg,
      press_color: T.press,
      click_func: openApp,
    })
  },

  render() {
    this.state.widgets.forEach((w) => deleteWidget(w))
    this.state.widgets = []

    const T = loadTheme()

    // Fundo (sem clique)
    this.add(widget.FILL_RECT, {
      x: 0,
      y: 0,
      w: W,
      h: W,
      color: T.bg,
    })

    this.add(widget.TEXT, {
      x: 0,
      y: 40,
      w: W,
      h: 38,
      color: T.accent,
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
        color: T.muted,
        text_size: 24,
        align_h: align.CENTER_H,
        text_style: text_style.WRAP,
        text: 'Sem dados ainda. Abra o app para carregar.',
      })
      this.addFooter(T, 'toque p/ abrir')
      return
    }

    let y = 96
    const accounts = data.accounts.slice(0, 3)
    accounts.forEach((acc) => {
      // Nome da conta
      this.add(widget.TEXT, {
        x: MX,
        y,
        w: CW - 110,
        h: 30,
        color: T.text,
        text_size: 24,
        align_h: align.LEFT,
        text_style: text_style.ELLIPSIS,
        text: acc.label || acc.typeLabel || '?',
      })

      if (acc.error) {
        this.add(widget.TEXT, {
          x: MX + CW - 110,
          y,
          w: 110,
          h: 30,
          color: T.err,
          text_size: 24,
          align_h: align.RIGHT,
          text: 'erro',
        })
        y += 40
        return
      }

      const m = findMain(acc)
      if (!m) {
        this.add(widget.TEXT, {
          x: MX + CW - 110,
          y,
          w: 110,
          h: 30,
          color: T.muted,
          text_size: 24,
          align_h: align.RIGHT,
          text: '-',
        })
        y += 40
        return
      }

      const pct = Math.min(100, m.percent || 0)
      this.add(widget.TEXT, {
        x: MX + CW - 110,
        y,
        w: 110,
        h: 30,
        color: pctColor(T, pct),
        text_size: 24,
        align_h: align.RIGHT,
        text: Math.round(m.percent || 0) + '%',
      })
      y += 32

      // Barra de progresso do limite de 5h
      this.add(widget.FILL_RECT, {
        x: MX,
        y,
        w: CW,
        h: 8,
        radius: 4,
        color: T.barBg,
      })
      this.add(widget.FILL_RECT, {
        x: MX,
        y,
        w: Math.max(8, Math.round((pct / 100) * CW)),
        h: 8,
        radius: 4,
        color: pctColor(T, pct),
      })
      y += 12

      // Quanto falta para zerar
      const rt = resetText(m)
      if (rt) {
        this.add(widget.TEXT, {
          x: MX,
          y,
          w: CW,
          h: 24,
          color: T.muted,
          text_size: 18,
          align_h: align.LEFT,
          text_style: text_style.ELLIPSIS,
          text: rt,
        })
        y += 26
      }
      y += 10
    })

    if (data.accounts.length > 3) {
      this.add(widget.TEXT, {
        x: MX,
        y,
        w: CW,
        h: 24,
        color: T.muted,
        text_size: 18,
        align_h: align.CENTER_H,
        text: '+' + (data.accounts.length - 3) + ' no app',
      })
    }

    this.addFooter(T, 'Atualizado ' + (data.updated_at || '--') + ' · toque p/ abrir')
  },
})
