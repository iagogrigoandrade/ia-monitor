import { BaseSideService } from '@zeppos/zml/base-side'

// Codifica "usuario:senha" em Base64 para o Basic Auth do painel
const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
function b64encode(str) {
  // UTF-8 primeiro (senhas com acento funcionam)
  const bytes = []
  for (let i = 0; i < str.length; i++) {
    let c = str.charCodeAt(i)
    if (c < 0x80) bytes.push(c)
    else if (c < 0x800) bytes.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f))
    else bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f))
  }
  let out = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const b1 = bytes[i]
    const b2 = bytes[i + 1]
    const b3 = bytes[i + 2]
    out += B64[b1 >> 2]
    out += B64[((b1 & 3) << 4) | (b2 === undefined ? 0 : b2 >> 4)]
    out += b2 === undefined ? '=' : B64[((b2 & 15) << 2) | (b3 === undefined ? 0 : b3 >> 6)]
    out += b3 === undefined ? '=' : B64[b3 & 63]
  }
  return out
}

function getSetting(key, fallback) {
  try {
    const v = settings.settingsStorage.getItem(key)
    if (v === null || v === undefined || v === '') return fallback
    return String(v)
  } catch (e) {
    return fallback
  }
}

// Horario local do celular (o painel manda o horario do servidor, em UTC)
function nowHHMMSS() {
  const d = new Date()
  const p = (n) => (n < 10 ? '0' + n : '' + n)
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
}

// Reduz o JSON do painel para caber com folga na transferencia Bluetooth
function shrink(data) {
  const accounts = (data.accounts || []).map((a) => {
    const out = {
      label: a.label || a.typeLabel || '',
      typeLabel: a.typeLabel || '',
    }
    if (a.error) out.error = String(a.error).slice(0, 120)
    if (a.stale) out.stale = String(a.stale).slice(0, 120)
    if (a.metrics) {
      out.metrics = a.metrics.map((m) => {
        const mm = { label: m.label || '' }
        if (typeof m.percent === 'number') mm.percent = m.percent
        if (m.value !== undefined) mm.value = m.value
        if (m.unit) mm.unit = m.unit
        if (m.reset) mm.reset = String(m.reset).slice(0, 40)
        return mm
      })
    }
    return out
  })
  return { accounts, updated_at: nowHHMMSS() }
}

AppSideService(
  BaseSideService({
    onInit() {},
    onRun() {},
    onDestroy() {},

    onRequest(req, res) {
      if (req.method !== 'GET_STATUS') {
        res(null, { error: 'Metodo desconhecido' })
        return
      }

      let base = getSetting('serverUrl', '').trim()
      if (!base) {
        res(null, {
          error: 'Configure o endereco do painel nas configuracoes do app (no Zepp do celular).',
        })
        return
      }
      if (!/^https?:\/\//i.test(base)) base = 'http://' + base
      base = base.replace(/\/+$/, '')

      const user = getSetting('user', 'admin')
      const pass = getSetting('password', '')
      const headers = { Accept: 'application/json' }
      if (pass) headers['Authorization'] = 'Basic ' + b64encode(user + ':' + pass)

      const url = base + '/api/status' + (req.force ? '?force=1' : '')

      fetch({ url, method: 'GET', headers })
        .then((resp) => {
          const status = resp.status || (resp.response && resp.response.code)
          let body = resp.body
          if (typeof body === 'string') {
            try {
              body = JSON.parse(body)
            } catch (e) {
              body = null
            }
          }
          if (status === 401) {
            res(null, { error: 'Senha do painel incorreta. Ajuste nas configuracoes.' })
            return
          }
          if (status === 429) {
            res(null, { error: 'Painel com muitas requisicoes (429). Aguarde um pouco.' })
            return
          }
          if (!body || !body.accounts) {
            res(null, { error: 'Resposta inesperada do painel (HTTP ' + status + ').' })
            return
          }
          res(null, shrink(body))
        })
        .catch((err) => {
          res(null, { error: 'Falha ao conectar no painel: ' + err })
        })
    },
  })
)
