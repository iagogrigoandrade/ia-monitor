import { notify } from '@zos/notification'
import * as appServiceMgr from '@zos/app-service'
import { readFileSync, writeFileSync } from '@zos/fs'
import { set as setAlarm } from '@zos/alarm'

// Vigia em segundo plano: acordado pelo alarme periodico (15 min) e por
// alarmes pontuais no horario de renovacao dos limites. Avisa quando o
// limite de 5h/semanal do Claude/Codex RENOVA (uso despenca), e se encerra.
const STATE_FILE = 'alerts_state.json'
const STATUS_FILE = 'last_status.json'

function readJson(path) {
  try {
    const raw = readFileSync({ path, options: { encoding: 'utf8' } })
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}

function writeJson(path, obj) {
  try {
    writeFileSync({ path, data: JSON.stringify(obj), options: { encoding: 'utf8' } })
  } catch (e) {}
}

function isMonitored(acc) {
  const t = String((acc.typeLabel || '') + ' ' + (acc.label || '')).toLowerCase()
  return t.indexOf('claude') >= 0 || t.indexOf('codex') >= 0 || t.indexOf('chatgpt') >= 0
}

function sendRenewedAlert(name, metric, pct) {
  try {
    notify({
      title: 'Monitor IA',
      content: name + ': ' + metric + ' renovado! Uso agora: ' + Math.round(pct) + '%',
      actions: [{ text: 'Abrir Monitor IA', file: 'page/index' }],
      vibrate: 4,
    })
  } catch (e) {}
}

function checkAndNotify(data) {
  // state[key] = { p: ultimo percentual visto, s: renovacao ja agendada (epoch) }
  const state = readJson(STATE_FILE) || {}
  const now = Math.floor(Date.now() / 1000)
  let changed = false
  const accounts = data.accounts || []
  accounts.forEach((acc) => {
    if (!isMonitored(acc) || acc.error) return
    const name = acc.label || acc.typeLabel || '?'
    const metrics = acc.metrics || []
    metrics.forEach((m) => {
      if (typeof m.percent !== 'number') return
      const key = name + '|' + (m.label || '')
      let prev = state[key]
      if (typeof prev !== 'object' || prev === null) prev = { p: null, s: 0 }
      const pct = m.percent

      // Renovou? Uso caiu muito desde a ultima olhada (a utilizacao so
      // cresce dentro da janela; queda forte = janela nova).
      if (
        prev.p !== null &&
        pct < prev.p &&
        (prev.p - pct >= 30 || (prev.p >= 10 && pct <= 5))
      ) {
        sendRenewedAlert(name, m.label || 'limite', pct)
      }
      if (prev.p !== pct) changed = true
      prev.p = pct

      // Agenda um despertador pontual para o horario exato da renovacao,
      // assim o aviso chega na hora (e nao ate 15 min depois).
      if (m.resetTs && m.resetTs > now && prev.s !== m.resetTs) {
        try {
          setAlarm({
            url: 'app-service/index',
            time: m.resetTs + 120, // +2 min p/ o painel ja refletir a renovacao
            param: 'src=reset',
            store: true,
          })
          prev.s = m.resetTs
          changed = true
        } catch (e) {}
      }

      state[key] = prev
    })
  })
  if (changed) writeJson(STATE_FILE, state)
}

function finish() {
  try {
    appServiceMgr.exit()
  } catch (e) {}
}

AppService({
  onInit(param) {
    try {
      const app = getApp()
      const globalData = app && app._options && app._options.globalData
      const messaging = globalData && globalData.messaging
      if (!messaging) {
        finish()
        return
      }
      // Quando acordado pelo alarme da renovacao, forca consulta fresca
      // (ignora o cache do painel) para ver o limite ja zerado.
      const force = String(param || '').indexOf('reset') >= 0
      messaging
        .request({ method: 'GET_STATUS', force }, { timeout: 25000 })
        .then((data) => {
          if (data && data.accounts) {
            // Aproveita e deixa o widget atualizado tambem
            writeJson(STATUS_FILE, data)
            checkAndNotify(data)
          }
          finish()
        })
        .catch(() => finish())
    } catch (e) {
      finish()
    }
  },

  onDestroy() {},
})
