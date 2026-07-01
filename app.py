#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de IA - painel local para acompanhar limites/creditos de:
  - Claude (limite de 5h e semanal)
  - Codex / ChatGPT (limite de 5h e semanal)
  - DeepSeek (saldo)
  - OpenRouter (creditos)

Roda em Windows, Linux e Mac usando SOMENTE a biblioteca padrao do Python 3.
Nao precisa instalar nada. Basta executar.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs, urlencode

# --------------------------------------------------------------------------
# Caminhos e configuracao
# --------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Permite apontar para outro arquivo de config (usado em testes p/ nao mexer no real)
CONFIG_PATH = os.environ.get("IA_MONITOR_CONFIG", os.path.join(BASE_DIR, "config.json"))
# Os backups ficam ao lado do config.json. Assim, apontando o config para um
# volume persistente (ex: /data/config.json), os backups tambem ficam salvos la.
BACKUP_DIR = os.path.join(os.path.dirname(CONFIG_PATH) or BASE_DIR, "backups")
HOME = os.path.expanduser("~")

# --------------------------------------------------------------------------
# Acesso remoto (Coolify/servidor): senha e endereco de escuta
# --------------------------------------------------------------------------
# Se IA_MONITOR_PASSWORD estiver definida, o painel exige login (usuario+senha).
# Sem ela, o painel abre sem senha (modo local na sua propria maquina).
AUTH_USER = os.environ.get("IA_MONITOR_USER", "admin")
AUTH_PASSWORD = os.environ.get("IA_MONITOR_PASSWORD", "")
# Endereco de escuta. Local: 127.0.0.1 (so a sua maquina). Servidor: 0.0.0.0.
LISTEN_HOST = os.environ.get("IA_MONITOR_HOST", "127.0.0.1")

# Onde os CLIs guardam o login OAuth (mesmo em qualquer sistema operacional)
CODEX_AUTH = os.path.join(HOME, ".codex", "auth.json")
CLAUDE_CRED = os.path.join(HOME, ".claude", ".credentials.json")

# IDs de cliente OAuth dos CLIs oficiais (usados para renovar o token)
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# OAuth do Claude (login feito pelo proprio painel, sem depender do CLI)
CLAUDE_AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
CLAUDE_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
CLAUDE_SCOPES = ("org:create_api_key user:profile user:inference "
                 "user:sessions:claude_code user:mcp_servers user:file_upload")
CLAUDE_UA = "claude-cli/2.0.0 (external, ia-monitor)"

DEFAULT_CONFIG = {
    "port": 8765,
    "refresh_seconds": 60,
    "accounts": [],
}

_config_lock = threading.RLock()  # reentrante: load_config pode chamar save_config

# Contexto SSL padrao (verifica certificados normalmente)
SSL_CTX = ssl.create_default_context()


def load_config():
    with _config_lock:
        if not os.path.exists(CONFIG_PATH):
            save_config(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg


def _backup_config():
    """Guarda uma copia do config atual antes de sobrescrever (contra perda de dados)."""
    try:
        if not os.path.exists(CONFIG_PATH):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(BACKUP_DIR, f"config-{stamp}.json")
        if not os.path.exists(dst):
            with open(CONFIG_PATH, "r", encoding="utf-8") as s, open(dst, "w", encoding="utf-8") as d:
                d.write(s.read())
        # mantem apenas os 20 backups mais recentes
        bks = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("config-"))
        for old in bks[:-20]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except Exception:
                pass
    except Exception:
        pass


def save_config(cfg):
    with _config_lock:
        _backup_config()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)


def update_account_creds(account_id, new_creds):
    """Grava de volta tokens renovados (rotacao de refresh token)."""
    cfg = load_config()
    for acc in cfg["accounts"]:
        if acc.get("id") == account_id:
            acc.setdefault("creds", {}).update(new_creds)
            break
    save_config(cfg)


# --------------------------------------------------------------------------
# Utilidades HTTP
# --------------------------------------------------------------------------

def http_json(url, method="GET", headers=None, data=None, timeout=25):
    headers = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urlrequest.Request(url, data=body, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"_raw": raw}
    except HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}
    except URLError as e:
        return 0, {"_error": str(e.reason)}
    except Exception as e:
        return 0, {"_error": str(e)}


def jwt_payload(token):
    """Le o payload de um JWT sem validar assinatura. Retorna dict vazio se falhar."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def jwt_exp(token):
    """Le o 'exp' de um JWT sem validar assinatura. Retorna timestamp ou None."""
    try:
        return jwt_payload(token).get("exp")
    except Exception:
        return None


def fmt_reset(iso_or_ts):
    """Transforma um horario de reset em texto amigavel: 'em 3h 20m'."""
    if not iso_or_ts:
        return None
    try:
        if isinstance(iso_or_ts, (int, float)):
            dt = datetime.fromtimestamp(iso_or_ts, tz=timezone.utc)
        else:
            s = iso_or_ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "agora"
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        if d > 0:
            return f"em {d}d {h}h"
        if h > 0:
            return f"em {h}h {m}m"
        return f"em {m}m"
    except Exception:
        return None


# --------------------------------------------------------------------------
# Provedor: Claude (limite de 5h e semanal)
# --------------------------------------------------------------------------

def claude_refresh(account_id, creds):
    rt = creds.get("refreshToken")
    if not rt:
        return None
    status, j = http_json(
        CLAUDE_TOKEN_URL,
        method="POST",
        headers={"User-Agent": CLAUDE_UA},
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": CLAUDE_CLIENT_ID,
        },
    )
    if status == 200 and j.get("access_token"):
        new = {
            "accessToken": j["access_token"],
            "refreshToken": j.get("refresh_token", rt),
            "expiresAt": int(time.time() * 1000) + int(j.get("expires_in", 3600)) * 1000,
        }
        update_account_creds(account_id, new)
        creds.update(new)
        return creds
    return None


def claude_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "User-Agent": "claude-cli/2.0.0 (external, ia-monitor)",
    }


_claude_profile_cache = {}  # account_id -> {"ts":..., "detail":...}


def _claude_profile(acc_id, token):
    """Perfil (email/plano) com cache longo — muda raramente e poupa requisicoes."""
    c = _claude_profile_cache.get(acc_id)
    if c and time.time() - c["ts"] < 1800:  # 30 min
        return c["detail"]
    _, prof = http_json(
        "https://api.anthropic.com/api/oauth/profile", headers=claude_headers(token))
    detail = None
    if isinstance(prof, dict) and prof.get("account"):
        email = prof["account"].get("email")
        org = prof.get("organization", {})
        plan = "Max" if prof["account"].get("has_claude_max") else (
            "Pro" if prof["account"].get("has_claude_pro") else org.get("organization_type", ""))
        detail = " ".join(x for x in [email, ("- " + plan) if plan else ""] if x)
        _claude_profile_cache[acc_id] = {"ts": time.time(), "detail": detail}
    elif c:
        return c["detail"]
    return detail


def provider_claude(acc):
    creds = dict(acc.get("creds", {}))
    token = creds.get("accessToken")
    if not token:
        return {"error": "Sem login do Claude. Adicione a conta com login pelo navegador ou QR."}

    exp = creds.get("expiresAt")
    if exp and time.time() * 1000 > (exp - 60000):
        claude_refresh(acc["id"], creds)
        token = creds.get("accessToken")

    status, usage = http_json(
        "https://api.anthropic.com/api/oauth/usage", headers=claude_headers(token))
    if status == 401:
        if claude_refresh(acc["id"], creds):
            token = creds["accessToken"]
            status, usage = http_json(
                "https://api.anthropic.com/api/oauth/usage", headers=claude_headers(token))
    if status == 429:
        return {"error": "Claude limitou as consultas (429). Vai voltar sozinho em instantes.", "transient": True}
    if status != 200:
        msg = usage.get("_error") or usage.get("error", {}).get("message") if isinstance(usage, dict) else None
        return {"error": f"Falha ao consultar Claude ({status}). {msg or 'Faca login novamente.'}"}

    five = usage.get("five_hour") or {}
    seven = usage.get("seven_day") or {}
    metrics = []
    if five:
        metrics.append({
            "label": "Limite 5h",
            "percent": round(float(five.get("utilization") or 0), 1),
            "reset": fmt_reset(five.get("resets_at")),
        })
    if seven:
        metrics.append({
            "label": "Limite semanal",
            "percent": round(float(seven.get("utilization") or 0), 1),
            "reset": fmt_reset(seven.get("resets_at")),
        })

    detail = _claude_profile(acc["id"], token)
    return {"metrics": metrics, "detail": detail, "kind": "percent"}


# --------------------------------------------------------------------------
# Provedor: Codex / ChatGPT (limite de 5h e semanal)
# --------------------------------------------------------------------------

def _codex_claims(creds):
    payload = jwt_payload(creds.get("id_token") or "")
    auth = payload.get("https://api.openai.com/auth") or {}
    profile = payload.get("https://api.openai.com/profile") or {}
    return payload, auth, profile


def _codex_account_id(creds):
    _, auth, _ = _codex_claims(creds)
    for value in (creds.get("account_id"), auth.get("chatgpt_account_id"), auth.get("account_id")):
        if value:
            return str(value).strip()
    return ""


def _codex_detail(creds, usage):
    usage = usage if isinstance(usage, dict) else {}
    payload, auth, profile = _codex_claims(creds)
    email = usage.get("email") or payload.get("email") or profile.get("email")
    plan = usage.get("plan_type") or auth.get("chatgpt_plan_type")
    if isinstance(plan, dict):
        plan = plan.get("type") or plan.get("name")
    if plan:
        plan = " ".join(x.capitalize() for x in str(plan).replace("-", "_").split("_") if x)
    return " ".join(x for x in [email, ("- " + plan) if plan else ""] if x)


def codex_refresh(account_id, creds):
    rt = creds.get("refresh_token")
    if not rt:
        return None
    status, j = http_json(
        "https://auth.openai.com/oauth/token",
        method="POST",
        data={
            "client_id": CODEX_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "scope": "openid profile email",
        },
    )
    if status == 200 and j.get("access_token"):
        new = {
            "access_token": j["access_token"],
            "refresh_token": j.get("refresh_token", rt),
            "id_token": j.get("id_token", creds.get("id_token")),
            "account_id": j.get("account_id") or creds.get("account_id"),
        }
        if not new.get("account_id"):
            new["account_id"] = _codex_account_id(new)
        update_account_creds(account_id, new)
        creds.update(new)
        return creds
    return None


def _codex_install_id():
    try:
        p = os.path.join(HOME, ".codex", "installation_id")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def codex_headers(creds):
    h = {
        "Authorization": "Bearer " + creds.get("access_token", ""),
        "originator": "codex_cli_rs",
        "User-Agent": "codex_cli_rs",
        "Accept": "application/json",
    }
    acc = _codex_account_id(creds)
    if acc:
        h["chatgpt-account-id"] = acc
        h["ChatGPT-Account-Id"] = acc
    iid = _codex_install_id()
    if iid:
        h["x-codex-installation-id"] = iid
    return h


def _codex_window_label(fallback, win):
    if not isinstance(win, dict):
        return fallback
    seconds = win.get("limit_window_seconds")
    minutes = win.get("window_minutes")
    if minutes is None:
        minutes = win.get("windowDurationMins")
    try:
        seconds = int(seconds if seconds is not None else 0)
        if seconds <= 0 and minutes is not None:
            seconds = int(minutes or 0) * 60
    except Exception:
        seconds = 0
    if seconds >= 604800:
        return "Limite semanal"
    if seconds >= 86400:
        days = round(seconds / 86400)
        return f"Limite {days}d"
    if seconds >= 3600:
        hours = round(seconds / 3600)
        return f"Limite {hours}h"
    return fallback


def _codex_window_metric(label, win):
    if not isinstance(win, dict):
        return None
    percent = win.get("used_percent")
    if percent is None:
        percent = win.get("usedPercent")
    if percent is None:
        return None
    try:
        percent = round(float(str(percent).strip().rstrip("%")), 1)
    except Exception:
        return None
    reset = win.get("reset_at")
    if reset is None:
        reset = win.get("resets_at")
    if reset is None:
        reset = win.get("resetsAt")
    if reset is None and win.get("reset_after_seconds") is not None:
        try:
            reset = time.time() + int(win.get("reset_after_seconds") or 0)
        except Exception:
            reset = None
    return {"label": _codex_window_label(label, win), "percent": percent, "reset": fmt_reset(reset)}


def _codex_parse(j):
    """Extrai % de uso dos formatos atuais/antigos do endpoint de uso do Codex."""
    metrics = []

    def add(label, win):
        m = _codex_window_metric(label, win)
        if m:
            metrics.append(m)

    def add_snapshot(snapshot):
        if not isinstance(snapshot, dict):
            return
        add("Limite 5h", snapshot.get("primary") or snapshot.get("primary_window"))
        add("Limite semanal", snapshot.get("secondary") or snapshot.get("secondary_window"))

    # Formato cru do backend atual: { rate_limit: { primary_window, secondary_window } }
    add_snapshot(j.get("rate_limit") or {})

    # Formato ja normalizado pelo app-server do Codex: { rate_limits: { primary, secondary } }
    rate_limits = j.get("rate_limits")
    if isinstance(rate_limits, dict):
        add_snapshot(rate_limits)
    elif isinstance(rate_limits, list):
        preferred = next((x for x in rate_limits if isinstance(x, dict) and x.get("limit_id") == "codex"), None)
        add_snapshot(preferred or (rate_limits[0] if rate_limits else {}))

    by_id = j.get("rate_limits_by_limit_id") or {}
    if isinstance(by_id, dict):
        add_snapshot(by_id.get("codex") or {})

    return metrics


CODEX_USAGE_URLS = [
    "https://chatgpt.com/backend-api/wham/usage",       # Codex CLI atual
    "https://chatgpt.com/backend-api/codex/usage",      # fallback antigo
]


def codex_usage_request(creds):
    last = (0, {"_error": "Nao consultei o Codex."})
    for url in CODEX_USAGE_URLS:
        status, j = http_json(url, headers=codex_headers(creds))
        last = (status, j)
        # Endpoint antigo/novo: so tenta o proximo quando o caminho nao existe.
        if status in (404, 405):
            continue
        return status, j
    return last


def provider_codex(acc):
    creds = dict(acc.get("creds", {}))
    if not creds.get("access_token"):
        return {"error": "Sem login do Codex. Adicione a conta com login pelo navegador ou QR."}

    # renova preventivamente se o token ja expirou
    exp = jwt_exp(creds["access_token"])
    if exp and time.time() > (exp - 60):
        codex_refresh(acc["id"], creds)

    def is_html(x):
        return isinstance(x, dict) and isinstance(x.get("_raw"), str) and \
            ("<html" in x["_raw"].lower() or x["_raw"].lstrip().startswith("<!"))

    status, j = codex_usage_request(creds)
    # 401 (ou 403 com erro JSON de token) = token vencido -> renova e tenta de novo.
    # 403 com pagina HTML = bloqueio temporario do Cloudflare (nao adianta renovar).
    token_problem = status == 401 or (status == 403 and isinstance(j, dict) and j.get("error"))
    if token_problem and codex_refresh(acc["id"], creds):
        status, j = codex_usage_request(creds)

    if status == 200 and isinstance(j, dict):
        metrics = _codex_parse(j)
        detail = _codex_detail(creds, j)
        if metrics:
            return {"metrics": metrics, "kind": "percent", "detail": detail}
        keys = ", ".join(sorted(k for k in j.keys() if not k.startswith("_"))[:6])
        return {"error": f"Codex respondeu, mas sem janelas de limite ({keys or 'sem dados'})."}

    if status == 429 or is_html(j) or (status == 403 and not (isinstance(j, dict) and j.get("error"))):
        return {"error": "Codex limitou as consultas por instantes. Volta sozinho.", "transient": True}
    if status == 401 or status == 403:
        return {"error": "Login do Codex expirado. Remova a conta e adicione de novo (navegador ou QR)."}
    msg = (j.get("error", {}) or {}).get("message") if isinstance(j, dict) else None
    return {"error": f"Falha ao consultar Codex ({status}). {msg or 'Verifique o login do Codex.'}"}


# --------------------------------------------------------------------------
# Provedor: DeepSeek (saldo)
# --------------------------------------------------------------------------

def provider_deepseek(acc):
    key = acc.get("api_key")
    if not key:
        return {"error": "Falta a API key do DeepSeek."}
    status, j = http_json(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    if status != 200:
        msg = j.get("error", {}).get("message") if isinstance(j, dict) else None
        return {"error": f"Falha DeepSeek ({status}). {msg or 'Verifique a API key.'}"}
    infos = j.get("balance_infos") or []
    metrics = []
    for info in infos:
        cur = info.get("currency", "")
        metrics.append({
            "label": f"Saldo {cur}".strip(),
            "value": info.get("total_balance"),
            "unit": cur,
        })
    avail = j.get("is_available")
    detail = "Disponivel" if avail else "Sem saldo utilizavel"
    return {"metrics": metrics, "kind": "value", "detail": detail}


# --------------------------------------------------------------------------
# Provedor: OpenRouter (creditos)
# --------------------------------------------------------------------------

def provider_openrouter(acc):
    key = acc.get("api_key")
    if not key:
        return {"error": "Falta a API key do OpenRouter."}
    status, j = http_json(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    if status != 200:
        msg = j.get("error", {}).get("message") if isinstance(j, dict) else None
        return {"error": f"Falha OpenRouter ({status}). {msg or 'Verifique a API key.'}"}
    data = j.get("data", {})
    total = float(data.get("total_credits") or 0)
    used = float(data.get("total_usage") or 0)
    remaining = total - used
    # 2 metricas (como o Claude): barra de uso + saldo restante; usado/comprado no detalhe
    if total > 0:
        pct = round((used / total * 100), 1)
        metrics = [
            {"label": "Uso dos creditos", "percent": pct},
            {"label": "Restante", "value": round(remaining, 4), "unit": "USD"},
        ]
        detail = f"Usado {round(used, 2)} de {round(total, 2)} USD"
    else:
        metrics = [{"label": "Usado", "value": round(used, 4), "unit": "USD"}]
        detail = "Pre-pago (sem limite de credito)"
    return {"metrics": metrics, "kind": "mixed", "detail": detail}


PROVIDERS = {
    "claude": provider_claude,
    "codex": provider_codex,
    "deepseek": provider_deepseek,
    "openrouter": provider_openrouter,
}

TYPE_LABELS = {
    "claude": "Claude",
    "codex": "Codex / ChatGPT",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
}


_result_cache = {}  # account_id -> {"ts":..., "result":...}  (ultimo resultado BOM)
_backoff = {}       # account_id -> {"until": ts, "count": n}  (recuo apos bloqueio)
_last_force = 0.0   # ultima vez que o botao "Atualizar" forcou consulta geral
BACKOFF_MAX = 900   # recuo maximo: 15 min

# Intervalo (segundos) do auto-refresh de cada provedor. DEFINIDO PELO SISTEMA
# com base no limite de consultas de cada um (o usuario nao edita, para nao
# quebrar). O botao "Atualizar agora" consulta todos na hora (respeitando so o
# recuo/backoff quando um provedor esta bloqueado).
#   codex:      atras do WAF Cloudflare, sensivel -> 300s (bem folgado)
#   claude:     endpoint OAuth de uso, propenso a 429 -> 120s
#   deepseek:   sem RPM fixo, endpoint leve -> 60s
#   openrouter: teto ~20 req/min -> 60s (folgado)
PROVIDER_INTERVAL = {
    "codex": 300,
    "claude": 120,
    "deepseek": 60,
    "openrouter": 60,
}
DEFAULT_INTERVAL = 120
STALE_MAX = 900  # ate 15 min mostrando o ultimo valor bom em caso de erro


def fetch_account(acc, force=False):
    aid = acc.get("id")
    atype = acc.get("type")
    fn = PROVIDERS.get(atype)
    interval = PROVIDER_INTERVAL.get(atype, DEFAULT_INTERVAL)  # definido pelo sistema
    base = {
        "id": aid,
        "type": atype,
        "typeLabel": TYPE_LABELS.get(atype, atype),
        "label": acc.get("label") or TYPE_LABELS.get(atype, ""),
        "interval": interval,
    }
    if not fn:
        base["error"] = "Tipo desconhecido."
        return base

    cached = _result_cache.get(aid)
    now = time.time()
    bo = _backoff.get(aid)

    def with_cache_or(msg):
        """Mostra o ultimo valor bom (marcado) ou, se nao houver, a mensagem."""
        if cached and now - cached["ts"] < STALE_MAX:
            keep = dict(cached["result"])
            keep["stale"] = msg
            base.update(keep)
        else:
            base["error"] = msg
        return base

    # 0) em recuo (backoff) apos bloqueio: NAO consulta (nem no "forcar"),
    #    para nao prolongar o bloqueio do provedor
    if bo and now < bo["until"]:
        wait = int(bo["until"] - now)
        return with_cache_or(f"Serviço limitou as consultas. Tentando de novo em ~{wait}s.")

    # 1) fora de "force": respeita o intervalo do provedor (reusa cache recente)
    if not force and cached and now - cached["ts"] < interval:
        base.update(cached["result"])
        return base

    try:
        result = fn(acc)
    except Exception as e:
        result = {"error": f"Erro interno: {e}"}

    # erro transitorio (429/WAF/Cloudflare) -> ativa recuo crescente e para de cutucar
    if result.get("transient"):
        count = (bo["count"] + 1) if bo else 1
        wait = min(30 * (2 ** count), BACKOFF_MAX)   # 60s, 120s, 240s, 480s, ... ate 15min
        _backoff[aid] = {"until": now + wait, "count": count}
        return with_cache_or(result["error"])

    # sucesso: limpa recuo e guarda cache (se tiver metricas)
    _backoff.pop(aid, None)
    if not result.get("error") and result.get("metrics"):
        _result_cache[aid] = {"ts": now, "result": result}

    # erro comum (nao transitorio): se houver valor bom recente, mostra ele
    if result.get("error"):
        return with_cache_or(result["error"])

    base.update(result)
    return base


def fetch_all(force=False):
    cfg = load_config()
    accts = cfg["accounts"]
    results = [None] * len(accts)
    threads = []

    def worker(i, a):
        results[i] = fetch_account(a, force=force)

    for i, a in enumerate(accts):
        t = threading.Thread(target=worker, args=(i, a))
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=40)

    return {
        "accounts": [r for r in results if r],
        "refresh_seconds": cfg.get("refresh_seconds", 60),
        "updated_at": datetime.now().strftime("%H:%M:%S"),
    }


# --------------------------------------------------------------------------
# Captura de login dos CLIs (para adicionar contas)
# --------------------------------------------------------------------------

def capture_codex():
    if not os.path.exists(CODEX_AUTH):
        return None, "Nao encontrei o login do Codex. Rode 'codex' e faca login primeiro."
    try:
        with open(CODEX_AUTH, "r", encoding="utf-8") as f:
            a = json.load(f)
        tk = a.get("tokens", {})
        creds = {
            "access_token": tk.get("access_token"),
            "refresh_token": tk.get("refresh_token"),
            "id_token": tk.get("id_token"),
            "account_id": tk.get("account_id"),
        }
        if not creds["access_token"]:
            return None, "Login do Codex incompleto. Faca login novamente."
        if not creds.get("account_id"):
            creds["account_id"] = _codex_account_id(creds)
        return creds, None
    except Exception as e:
        return None, f"Erro lendo login do Codex: {e}"


def capture_claude():
    if not os.path.exists(CLAUDE_CRED):
        return None, "Nao encontrei o login do Claude. Rode 'claude' e faca login primeiro."
    try:
        with open(CLAUDE_CRED, "r", encoding="utf-8") as f:
            a = json.load(f)
        o = a.get("claudeAiOauth", {})
        creds = {
            "accessToken": o.get("accessToken"),
            "refreshToken": o.get("refreshToken"),
            "expiresAt": o.get("expiresAt"),
        }
        if not creds["accessToken"]:
            return None, "Login do Claude incompleto. Faca login novamente."
        return creds, None
    except Exception as e:
        return None, f"Erro lendo login do Claude: {e}"


# --------------------------------------------------------------------------
# Login pelo navegador (dispara o login oficial do CLI e captura o resultado)
# --------------------------------------------------------------------------

# Como cada login funciona:
#   codex  (kind=cli)   -> roda o CLI oficial. No PC: login loopback (localhost:1455),
#                          captura sozinho. No celular: 'login --device-auth' mostra
#                          URL + codigo, nao abre navegador no PC.
#   claude (kind=oauth) -> o proprio painel gera a URL OAuth e troca o codigo. Assim
#                          controlamos a janela e o QR funciona sem abrir nada no PC.

_login_lock = threading.Lock()
_login_jobs = {}  # id -> job
LOGIN_ACTIVE = ("starting", "awaiting", "working")


def is_loopback_host(host_header):
    host = (host_header or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):
        host = host.split("]", 1)[0].strip("[]")
    else:
        host = host.split(":", 1)[0]
    return host == "localhost" or host == "::1" or host.startswith("127.")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip(s):
    return ANSI_RE.sub("", s)


def _b64url(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _pkce():
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


class LoginJob:
    def __init__(self, provider, label, method):
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.label = label
        self.method = method            # "browser" | "phone"
        self.kind = "oauth" if provider == "claude" else "cli"
        # mode diz para a interface o que mostrar:
        #   loopback -> so aguarda (codex no PC)
        #   device   -> mostra QR + codigo, aguarda (codex no celular)
        #   paste    -> mostra QR/link e pede para colar o codigo (claude)
        self.mode = "paste" if provider == "claude" else (
            "device" if method == "phone" else "loopback")
        self.status = "starting"        # starting|awaiting|working|done|error
        self.message = ""
        self.url = ""
        self.code = ""                  # codigo de device (codex celular)
        self.account_id = None
        self.proc = None
        self.started = time.time()
        self.lines = []
        # so para claude (oauth):
        self.verifier = None
        self.state = None


def _find_url(text):
    m = re.search(r"https://\S*oauth/authorize\S*", text)
    if m:
        return m.group(0)
    m = re.search(r"https://[^\s>]+", text)
    return m.group(0) if m else None


def _find_device_code(text):
    m = re.search(r"\b([A-Z0-9]{3,5}-[A-Z0-9]{3,6})\b", text)
    return m.group(1) if m else None


def _save_account(provider, label, creds):
    acc = {"id": uuid.uuid4().hex[:12], "type": provider,
           "label": label or TYPE_LABELS[provider], "creds": creds}
    cfg = load_config()
    cfg["accounts"].append(acc)
    save_config(cfg)
    return acc["id"]


def _cancel_login_job(job, message="Cancelado."):
    job.status = "error"
    job.message = message
    try:
        if job.proc and job.proc.poll() is None:
            job.proc.kill()
    except Exception:
        pass


def _refresh_login_jobs():
    """Marca como encerrados processos que morreram sem atualizar o status."""
    for job in _login_jobs.values():
        try:
            if job.status in LOGIN_ACTIVE and job.proc and job.proc.poll() is not None:
                job.status = "error"
                job.message = job.message or "Login interrompido. Tente novamente."
        except Exception:
            pass


# ---- Codex: roda o CLI e captura o resultado -----------------------------

def _codex_worker(job):
    exe = shutil.which("codex")
    if not exe:
        job.status = "error"
        job.message = "Nao encontrei o programa 'codex'. Instale o CLI do Codex primeiro."
        return
    args = ["login", "--device-auth"] if job.method == "phone" else ["login"]
    try:
        job.proc = subprocess.Popen(
            [exe] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except Exception as e:
        job.status = "error"
        job.message = f"Falha ao iniciar login: {e}"
        return

    for raw in job.proc.stdout:
        line = _strip(raw.rstrip())
        job.lines.append(line)
        if not job.url:
            u = _find_url(line)
            if u:
                job.url = u
                if job.status == "starting":
                    job.status = "awaiting"
        if job.method == "phone" and not job.code:
            c = _find_device_code(line)
            if c:
                job.code = c

    rc = job.proc.wait()
    if job.status == "error":
        return
    if rc == 0:
        creds, err = capture_codex()
        if err:
            job.status = "error"
            job.message = err
            return
        job.account_id = _save_account("codex", job.label, creds)
        job.status = "done"
        job.message = "Login concluido!"
    else:
        tail = " ".join(job.lines[-4:]) if job.lines else ""
        job.status = "error"
        job.message = f"Login nao concluido (codigo {rc}). {tail}".strip()


# ---- Claude: OAuth feito pelo proprio painel -----------------------------

def _claude_build_url(job):
    job.verifier, challenge = _pkce()
    job.state = _b64url(secrets.token_bytes(24))
    params = {
        "code": "true",
        "client_id": CLAUDE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "scope": CLAUDE_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": job.state,
    }
    job.url = CLAUDE_AUTHORIZE_URL + "?" + urlencode(params)
    job.status = "awaiting"


def _claude_exchange(job, pasted):
    raw = (pasted or "").strip()
    if not raw:
        return "Cole o codigo primeiro."
    code = raw.split("#")[0].split("&")[0].strip()
    state = job.state
    if "#" in raw:
        state = raw.split("#", 1)[1].strip() or job.state
    status, j = http_json(
        CLAUDE_TOKEN_URL, method="POST",
        headers={"User-Agent": CLAUDE_UA},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "state": state,
            "client_id": CLAUDE_CLIENT_ID,
            "redirect_uri": CLAUDE_REDIRECT_URI,
            "code_verifier": job.verifier,
        },
    )
    if status == 200 and j.get("access_token"):
        creds = {
            "accessToken": j["access_token"],
            "refreshToken": j.get("refresh_token"),
            "expiresAt": int(time.time() * 1000) + int(j.get("expires_in", 3600)) * 1000,
        }
        job.account_id = _save_account("claude", job.label, creds)
        job.status = "done"
        job.message = "Login concluido!"
        return None
    desc = j.get("error_description") or j.get("error") or f"codigo {status}"
    return f"Codigo invalido ou expirado ({desc}). Tente novamente."


def _login_watchdog(job, timeout=600):
    while time.time() < job.started + timeout:
        if job.status in ("done", "error"):
            return
        time.sleep(2)
    if job.status not in ("done", "error"):
        _cancel_login_job(job, "Tempo esgotado. Tente novamente.")


def start_login(provider, label, method, replace=False):
    if provider not in ("codex", "claude"):
        return None, "Login nao disponivel para este servico."
    if method not in ("browser", "phone"):
        method = "browser"
    with _login_lock:
        _refresh_login_jobs()
        active = [j for j in _login_jobs.values() if j.status in LOGIN_ACTIVE]
        if active and not replace:
            return None, "Ja existe um login em andamento. Conclua ou cancele antes."
        for j in active:
            _cancel_login_job(j, "Cancelado por nova tentativa.")
        job = LoginJob(provider, label, method)
        _login_jobs[job.id] = job
    if job.kind == "oauth":       # claude
        _claude_build_url(job)
    else:                          # codex
        threading.Thread(target=_codex_worker, args=(job,), daemon=True).start()
    threading.Thread(target=_login_watchdog, args=(job,), daemon=True).start()
    return job, None


def login_submit_code(job_id, code):
    job = _login_jobs.get(job_id)
    if not job:
        return "Login nao encontrado."
    if job.kind == "oauth":       # claude troca o codigo aqui mesmo
        job.status = "working"
        err = _claude_exchange(job, code)
        if err:
            job.status = "awaiting"   # deixa tentar de novo
        return err
    # codex nao usa codigo colado (loopback/device se resolvem sozinhos)
    return "Este login se conclui sozinho, nao precisa colar codigo."


def login_cancel(job_id):
    job = _login_jobs.get(job_id)
    if job:
        _cancel_login_job(job)


# --------------------------------------------------------------------------
# Servidor HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silencioso

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _check_auth(self):
        """Exige usuario+senha se IA_MONITOR_PASSWORD estiver definida.
        Sem senha configurada (modo local), libera tudo."""
        if not AUTH_PASSWORD:
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                raw = base64.b64decode(hdr[6:]).decode("utf-8")
                user, _, pw = raw.partition(":")
                ok_user = hmac.compare_digest(user, AUTH_USER)
                ok_pw = hmac.compare_digest(pw, AUTH_PASSWORD)
                if ok_user and ok_pw:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Monitor de IA"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        # Verificacao de saude do Coolify (sem senha, so diz "estou vivo")
        if path == "/healthz":
            self._send(200, {"ok": True})
            return
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        if path == "/" or path.startswith("/index"):
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
        elif path == "/api/status":
            force = (query.get("force") or ["0"])[0] == "1"
            if force:
                # protecao contra clique-duplo: no maximo 1 "forcar" a cada 4s
                global _last_force
                if time.time() - _last_force < 4:
                    force = False
                else:
                    _last_force = time.time()
            self._send(200, fetch_all(force=force))
        elif path == "/api/accounts":
            cfg = load_config()
            safe = [{"id": a["id"], "type": a.get("type"), "label": a.get("label"),
                     "typeLabel": TYPE_LABELS.get(a.get("type"), a.get("type")),
                     "interval": PROVIDER_INTERVAL.get(a.get("type"), DEFAULT_INTERVAL)}
                    for a in cfg["accounts"]]
            self._send(200, {"accounts": safe, "refresh_seconds": cfg.get("refresh_seconds", 60)})
        elif path == "/api/login/status":
            jid = (query.get("id") or [""])[0]
            job = _login_jobs.get(jid)
            if not job:
                self._send(404, {"error": "Login nao encontrado."})
                return
            self._send(200, {"status": job.status, "mode": job.mode, "method": job.method,
                             "url": job.url, "code": job.code, "message": job.message,
                             "account_id": job.account_id})
        elif path == "/qrcode.min.js":
            qpath = os.path.join(BASE_DIR, "qrcode.min.js")
            if os.path.exists(qpath):
                with open(qpath, "rb") as f:
                    self._send(200, f.read(), "application/javascript; charset=utf-8")
            else:
                self._send(404, "// qrcode lib ausente", "application/javascript")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._check_auth():
            return
        body = self._read_json()
        if self.path == "/api/accounts/add":
            self._handle_add(body)
        elif self.path == "/api/accounts/delete":
            self._handle_delete(body)
        elif self.path == "/api/accounts/reorder":
            self._handle_reorder(body)
        elif self.path == "/api/settings":
            cfg = load_config()
            if "refresh_seconds" in body:
                try:
                    cfg["refresh_seconds"] = max(15, int(body["refresh_seconds"]))
                except Exception:
                    pass
            save_config(cfg)
            self._send(200, {"ok": True})
        elif self.path == "/api/login/start":
            if body.get("type") == "codex" and (body.get("method") or "browser") == "browser" and \
                    not is_loopback_host(self.headers.get("Host", "")):
                self._send(400, {"error": "No deploy, o login 'Neste PC' do Codex nao funciona porque o retorno usa localhost:1455. Use 'No celular (QR)'."})
                return
            job, err = start_login(body.get("type"), (body.get("label") or "").strip(),
                                   body.get("method") or "browser", bool(body.get("replace")))
            if err:
                self._send(400, {"error": err})
            else:
                self._send(200, {"ok": True, "id": job.id, "mode": job.mode,
                                 "method": job.method})
        elif self.path == "/api/login/code":
            err = login_submit_code(body.get("id"), body.get("code"))
            self._send(200 if not err else 400, {"ok": not err, "error": err})
        elif self.path == "/api/login/cancel":
            login_cancel(body.get("id"))
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def _handle_add(self, body):
        atype = body.get("type")
        label = (body.get("label") or "").strip()
        if atype not in PROVIDERS:
            self._send(400, {"error": "Tipo invalido."})
            return
        acc = {"id": uuid.uuid4().hex[:12], "type": atype, "label": label or TYPE_LABELS[atype]}

        if atype in ("deepseek", "openrouter"):
            key = (body.get("api_key") or "").strip()
            if not key:
                self._send(400, {"error": "Cole a API key."})
                return
            acc["api_key"] = key
        elif atype == "codex":
            creds, err = capture_codex()
            if err:
                self._send(400, {"error": err})
                return
            acc["creds"] = creds
        elif atype == "claude":
            creds, err = capture_claude()
            if err:
                self._send(400, {"error": err})
                return
            acc["creds"] = creds

        cfg = load_config()
        cfg["accounts"].append(acc)
        save_config(cfg)
        self._send(200, {"ok": True, "id": acc["id"]})

    def _handle_delete(self, body):
        aid = body.get("id")
        cfg = load_config()
        before = len(cfg["accounts"])
        cfg["accounts"] = [a for a in cfg["accounts"] if a.get("id") != aid]
        save_config(cfg)
        _result_cache.pop(aid, None)
        self._send(200, {"ok": True, "removed": before - len(cfg["accounts"])})

    def _handle_reorder(self, body):
        ids = body.get("ids") or []
        cfg = load_config()
        by_id = {a.get("id"): a for a in cfg["accounts"]}
        new_order = [by_id[i] for i in ids if i in by_id]
        # preserva quaisquer contas que nao vieram na lista (seguranca)
        for a in cfg["accounts"]:
            if a not in new_order:
                new_order.append(a)
        cfg["accounts"] = new_order
        save_config(cfg)
        self._send(200, {"ok": True})


# --------------------------------------------------------------------------
# HTML / UI (embutido, sem arquivos externos)
# --------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor de IA</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0F172A; --bg2:#0b1120; --surface:#141d31; --surface2:#1b2740;
    --line:#26324a; --line2:#334155;
    --txt:#F8FAFC; --muted:#94a3b8; --faint:#64748b;
    --accent:#22C55E; --accent-ink:#052e16;
    --ok:#22C55E; --warn:#F5A623; --bad:#EF4444;
    --claude:#d97757; --codex:#10b981; --deepseek:#4f8cff; --openrouter:#a855f7;
    --r:14px; --r-sm:9px; --sp:8px;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
    --ease:cubic-bezier(.2,.7,.3,1);
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;font-family:"Fira Sans","Segoe UI",Roboto,system-ui,sans-serif;
    background:radial-gradient(1200px 600px at 80% -10%, #16203a 0%, var(--bg) 55%) fixed;
    color:var(--txt);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}
  .mono{font-family:"Fira Code",ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}

  header{display:flex;align-items:center;flex-wrap:wrap;gap:10px 14px;padding:12px clamp(12px,4vw,28px);
    border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;
    background:rgba(15,23,42,.82);backdrop-filter:saturate(140%) blur(10px)}
  .brand{display:flex;align-items:center;gap:11px}
  .brand .logo{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;
    background:linear-gradient(145deg,#1e293b,#0b1324);border:1px solid var(--line2);color:var(--accent)}
  .brand h1{font-size:17px;margin:0;font-weight:600;letter-spacing:.2px}
  .brand .sub{font-size:11px;color:var(--faint);margin-top:1px}
  header .spacer{flex:1}
  header .btns{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  #updated{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;white-space:nowrap}
  #updated .dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:0 0 8px var(--accent)}

  button{cursor:pointer;border:1px solid var(--line2);background:var(--surface2);color:var(--txt);
    border-radius:var(--r-sm);padding:9px 14px;font-size:13px;font-family:inherit;font-weight:500;
    display:inline-flex;align-items:center;gap:7px;transition:all .18s var(--ease);touch-action:manipulation}
  button:hover{border-color:var(--accent);transform:translateY(-1px)}
  button:active{transform:translateY(0)}
  button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  button svg{width:16px;height:16px}
  button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
  button.primary:hover{filter:brightness(1.08)}
  button.ghost{background:transparent}
  button.danger{border-color:#4b2226;color:#fca5a5;background:transparent}
  button.danger:hover{border-color:var(--bad);background:rgba(239,68,68,.08)}

  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));
    gap:16px;padding:clamp(12px,4vw,28px);max-width:1400px;margin:0 auto;align-items:stretch}
  .card{background:linear-gradient(180deg,var(--surface),var(--bg2));border:1px solid var(--line);
    border-radius:var(--r);padding:18px 18px 14px;position:relative;box-shadow:var(--shadow);
    display:flex;flex-direction:column;min-height:250px;
    transition:border-color .18s var(--ease),transform .18s var(--ease),box-shadow .18s var(--ease)}
  .card:hover{border-color:var(--line2)}
  .card.dragging{opacity:.4}
  .card.drop-target{border-color:var(--accent);border-style:dashed}
  .card .grip{position:absolute;top:14px;right:12px;color:var(--faint);cursor:grab;
    display:flex;gap:2px;padding:4px;border-radius:6px}
  .card .grip:hover{color:var(--muted);background:var(--surface2)}
  .card .grip:active{cursor:grabbing}
  .card .top{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-right:22px}
  .ic{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;flex-shrink:0}
  .ic svg{width:18px;height:18px}
  .i-claude{background:rgba(217,119,87,.14);color:var(--claude)}
  .i-codex{background:rgba(16,185,129,.14);color:var(--codex)}
  .i-deepseek{background:rgba(79,140,255,.14);color:var(--deepseek)}
  .i-openrouter{background:rgba(168,85,247,.14);color:var(--openrouter)}
  .card h3{margin:0;font-size:15px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .card .badge{font-size:10.5px;color:var(--muted);font-weight:500;letter-spacing:.3px;text-transform:uppercase}
  .card .detail{font-size:11.5px;color:var(--faint);margin:-6px 0 12px 42px;word-break:break-all}

  .metric{margin:14px 0}
  .metric .row{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;margin-bottom:6px}
  .metric .row .lbl{color:var(--muted)}
  .metric .row .pct{font-weight:600}
  .metric .row .r{color:var(--faint);font-size:11px;font-weight:400}
  .bar{height:8px;background:#0a1120;border-radius:20px;overflow:hidden;border:1px solid var(--line)}
  .bar span{display:block;height:100%;border-radius:20px;transition:width .6s var(--ease)}
  .val{font-size:26px;font-weight:700}
  .val small{font-size:13px;color:var(--muted);font-weight:400;margin-left:5px}
  .err{background:rgba(239,68,68,.08);border:1px solid #4b2226;color:#fca5a5;
    padding:10px 12px;border-radius:var(--r-sm);font-size:12.5px;line-height:1.45}
  .stale{margin-top:10px;font-size:11px;color:var(--warn);display:flex;align-items:center;gap:5px}

  .cfoot{display:flex;align-items:center;justify-content:space-between;gap:8px;
    margin-top:auto;padding-top:11px;border-top:1px solid var(--line)}
  .cfoot .auto{font-size:11.5px;color:var(--faint);display:flex;align-items:center;gap:6px}
  .cfoot .auto b{color:var(--muted);font-weight:600}
  .cfoot .acts{display:flex;gap:4px}
  .iconbtn{width:36px;height:36px;padding:0;border-radius:8px;background:transparent;border:1px solid transparent;
    color:var(--faint);display:grid;place-items:center;flex-shrink:0;touch-action:manipulation}
  .iconbtn:hover{color:var(--txt);border-color:var(--line2);background:var(--surface2);transform:none}
  .iconbtn.danger:hover{color:var(--bad);border-color:#4b2226}
  .iconbtn svg{width:15px;height:15px}

  .empty{padding:70px 22px;text-align:center;color:var(--muted);max-width:420px;margin:0 auto}
  .empty .logo{width:56px;height:56px;margin:0 auto 18px;border-radius:16px;display:grid;place-items:center;
    background:var(--surface);border:1px solid var(--line2);color:var(--accent)}
  .empty h2{color:var(--txt);font-size:18px;margin:0 0 8px}

  /* modal */
  .overlay{position:fixed;inset:0;background:rgba(3,7,18,.72);display:none;align-items:center;
    justify-content:center;z-index:20;padding:16px;backdrop-filter:blur(4px)}
  .overlay.show{display:flex;animation:fade .2s var(--ease)}
  @keyframes fade{from{opacity:0}to{opacity:1}}
  .modal{background:linear-gradient(180deg,var(--surface),var(--bg2));border:1px solid var(--line2);
    border-radius:var(--r);padding:24px;width:min(520px,96vw);max-height:92vh;overflow:auto;
    box-shadow:0 24px 60px -20px rgba(0,0,0,.8);animation:pop .22s var(--ease)}
  @keyframes pop{from{opacity:0;transform:scale(.97) translateY(8px)}to{opacity:1;transform:none}}
  .modal h2{margin:0 0 4px;font-size:19px;font-weight:600}
  .modal p.sub{margin:0 0 8px;color:var(--muted);font-size:13px}
  label{display:block;font-size:12.5px;margin:16px 0 6px;color:var(--muted);font-weight:500}
  input,select{width:100%;padding:11px;background:var(--bg2);border:1px solid var(--line2);
    border-radius:var(--r-sm);color:var(--txt);font-size:14px;font-family:inherit;transition:border-color .15s}
  input:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(34,197,94,.15)}
  .hint{font-size:12.5px;color:var(--muted);margin-top:10px;line-height:1.6}
  .hint code{background:var(--bg2);padding:1px 6px;border-radius:5px;font-family:"Fira Code",monospace;font-size:11.5px}
  .modal .actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}
  .msg{margin-top:12px;font-size:13px}
  .msg.ok{color:var(--ok)} .msg.bad{color:var(--bad)}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  /* login */
  #qrbox{display:flex;justify-content:center;margin:12px 0}
  #qrbox svg{width:210px;height:210px;background:#fff;border-radius:12px;padding:10px}
  .devcode{font-size:30px;font-weight:700;letter-spacing:4px;text-align:center;background:var(--bg2);
    border:1px dashed var(--line2);border-radius:12px;padding:14px;margin:10px 0;color:var(--accent);
    font-family:"Fira Code",monospace}
  .choice{display:flex;gap:10px;flex-wrap:wrap}
  .choice button{flex:1;min-width:140px;padding:16px;justify-content:center;flex-direction:column;gap:6px}
  .choice button svg{width:22px;height:22px}
  .steps{font-size:13px;line-height:1.7;color:var(--muted)}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
  /* ---- tablet / celular grande ------------------------------------------- */
  @media (max-width:768px){
    header{gap:10px}
    header .spacer{display:none}
    .brand .sub{display:none}
    button{padding:8px 12px;font-size:12.5px}
    .grid{grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
    .card{min-height:220px}
  }
  /* ---- celular pequeno --------------------------------------------------- */
  @media (max-width:480px){
    header{padding:10px 12px;gap:8px}
    header .btns{width:100%;justify-content:space-between}
    header .btns button{flex:1}
    #btnRefresh{min-width:0;padding:9px 8px;font-size:12px;justify-content:center}
    #btnAdd{min-width:0;padding:9px 8px;font-size:12px;justify-content:center}
    .grid{grid-template-columns:1fr;gap:12px;padding:12px}
    .card{padding:14px 14px 10px;min-height:200px;border-radius:12px}
    .card .top{margin-bottom:10px}
    .card h3{font-size:14px}
    .card .badge{font-size:10px}
    .card .detail{font-size:11px;margin:-4px 0 10px 42px}
    .val{font-size:22px}
    .val small{font-size:11.5px}
    .metric{margin:10px 0}
    .metric .row{font-size:12px}
    .err{font-size:12px;padding:8px 10px}
    .cfoot{gap:6px;padding-top:8px}
    .cfoot .auto{font-size:10.5px}
    .iconbtn{width:34px;height:34px}
    .modal{padding:18px;border-radius:12px}
    .modal h2{font-size:17px}
    .modal p.sub{font-size:12px}
    label{font-size:12px;margin:12px 0 4px}
    input,select{padding:10px;font-size:16px}
    .devcode{font-size:22px;letter-spacing:2px;padding:10px}
    #qrbox svg{width:170px;height:170px}
    .choice button{padding:14px 10px;min-width:130px;font-size:12px}
    .overlay{padding:10px}
    .empty{padding:40px 16px}
    .empty h2{font-size:16px}
  }

</style>
<script src="/qrcode.min.js"></script>
</head>
<body>
<header>
  <div class="brand">
    <div class="logo" id="brandlogo"></div>
    <div>
      <h1>Monitor de IA</h1>
      <div class="sub">uso e créditos das suas IAs</div>
    </div>
  </div>
  <div class="spacer"></div>
  <div class="btns">
    <span id="updated"></span>
    <button class="ghost" onclick="load(true)" id="btnRefresh">Atualizar agora</button>
    <button class="primary" onclick="openAdd()" id="btnAdd">Adicionar conta</button>
  </div>
</header>

<div id="grid" class="grid"></div>
<div id="empty" class="empty" style="display:none">
  <div class="logo" id="emptylogo"></div>
  <h2>Nenhuma conta ainda</h2>
  <p>Adicione Claude, Codex, DeepSeek ou OpenRouter para acompanhar o uso e os créditos em um só lugar.</p>
  <button class="primary" style="margin-top:16px" onclick="openAdd()">Adicionar primeira conta</button>
</div>

<div class="overlay" id="overlay">
  <div class="modal">
    <h2>Adicionar conta</h2>
    <p class="sub">Escolha o servico. Nada e enviado para a internet alem do proprio servico.</p>

    <label>Servico</label>
    <select id="f_type" onchange="onType()">
      <option value="claude">Claude (limite 5h e semanal)</option>
      <option value="codex">Codex / ChatGPT (limite 5h e semanal)</option>
      <option value="deepseek">DeepSeek (saldo)</option>
      <option value="openrouter">OpenRouter (creditos)</option>
    </select>

    <label>Nome da conta (para voce diferenciar)</label>
    <input id="f_label" placeholder="Ex: Codex Conta 1">

    <div id="key_box">
      <label>API Key</label>
      <input id="f_key" placeholder="cole a chave aqui" autocomplete="off">
    </div>

    <div id="cli_box" class="hint"></div>

    <div id="login_box" style="display:none">
      <div id="login_step" class="steps"></div>
      <div id="qrbox"></div>
      <div id="devcode_box" style="display:none">
        <div class="hint" style="text-align:center">Digite este codigo na pagina que abrir:</div>
        <div class="devcode" id="devcode"></div>
      </div>
      <div id="login_link" style="margin-top:10px;text-align:center"></div>
      <div id="code_box" style="display:none;margin-top:14px">
        <label>Cole aqui o codigo que o navegador/celular mostrou</label>
        <input id="f_code" placeholder="cole o codigo e clique em Concluir" autocomplete="off">
        <button class="primary" style="margin-top:10px;width:100%" onclick="submitLoginCode()">Concluir login</button>
      </div>
    </div>

    <div class="msg" id="modal_msg"></div>
    <div class="actions" id="modal_actions"></div>
  </div>
</div>

<script>
let timer = null;
let POLL = 15;          // cadencia base do cliente (o servidor respeita o limite de cada conta)

// ---- Icones SVG (Lucide) — sem emojis ------------------------------------
const ICON = {
  activity:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  refresh:'<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  trash:'<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  grip:'<circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/>',
  check:'<path d="M20 6L9 17l-5-5"/>',
  x:'<path d="M18 6L6 18M6 6l12 12"/>',
  monitor:'<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  phone:'<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
  bot:'<rect x="3" y="8" width="18" height="12" rx="3"/><path d="M12 8V4M8 2h8"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/>',
  wallet:'<path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M16 12h4"/>',
  cpu:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  link:'<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
};
function svg(name, w){ return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${w?` style="width:${w}px;height:${w}px"`:''}>${ICON[name]||''}</svg>`; }
const TYPEICON = {claude:'bot', codex:'cpu', deepseek:'activity', openrouter:'link'};

function color(p){ if(p>=90) return 'var(--bad)'; if(p>=70) return 'var(--warn)'; return 'var(--ok)'; }
function fmtNum(v){ return (typeof v==='number') ? v.toLocaleString('pt-BR',{maximumFractionDigits:4}) : v; }

function metricHTML(m){
  if(m.percent !== undefined && m.percent !== null){
    const p = Math.max(0, Math.min(100, m.percent));
    return `<div class="metric">
      <div class="row"><span class="lbl">${m.label}</span>
        <span><span class="pct mono">${m.percent}%</span> ${m.reset?`<span class="r">· ${m.reset}</span>`:''}</span></div>
      <div class="bar"><span style="width:${p}%;background:${color(p)}"></span></div>
    </div>`;
  }
  if(m.value !== undefined && m.value !== null){
    return `<div class="metric">
      <div class="row"><span class="lbl">${m.label}</span></div>
      <div class="val mono">${fmtNum(m.value)}<small>${m.unit||''}</small></div>
    </div>`;
  }
  return '';
}

function cardHTML(a){
  let inner;
  if(a.error){
    inner = `<div class="err">${a.error}</div>`;
  } else {
    inner = (a.metrics||[]).map(metricHTML).join('') || '<div class="err">Sem dados.</div>';
    if(a.stale){ inner += `<div class="stale">${svg('clock',13)} mostrando o último valor — atualizando…</div>`; }
  }
  const foot = `<div class="cfoot">
       <span class="auto" title="Definido automaticamente conforme o limite de consultas deste serviço">${svg('clock',13)} auto a cada <b>${a.interval}s</b></span>
       <span class="acts">
         <button class="iconbtn danger" title="Remover conta" onclick="delAcc('${a.id}')">${svg('trash')}</button>
       </span>
     </div>`;
  return `<div class="card" draggable="true" data-id="${a.id}">
    <span class="grip" title="Arraste para reordenar">${svg('grip')}</span>
    <div class="top">
      <span class="ic i-${a.type}">${svg(TYPEICON[a.type]||'activity')}</span>
      <div style="min-width:0">
        <h3>${a.label||a.typeLabel}</h3>
        <span class="badge">${a.typeLabel}</span>
      </div>
    </div>
    ${a.detail?`<div class="detail">${a.detail}</div>`:''}
    ${inner}
    ${foot}
  </div>`;
}

let LAST = [];  // ultimo snapshot de contas
function renderCards(){
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty');
  if(!LAST || LAST.length===0){ grid.innerHTML=''; empty.style.display='block'; return; }
  empty.style.display='none';
  grid.innerHTML = LAST.map(cardHTML).join('');
  setupDrag();
}

async function load(force){
  try{
    const r = await fetch('/api/status' + (force ? '?force=1' : ''));
    const d = await r.json();
    LAST = d.accounts || [];
    // cadencia de polling = menor intervalo entre as contas (limitado a 10–30s)
    const mn = LAST.reduce((m,a)=>Math.min(m, a.interval||60), 60);
    POLL = Math.max(10, Math.min(30, mn));
    // nao redesenha enquanto o usuario estiver arrastando um card
    if(!dragId) renderCards();
    const u = document.getElementById('updated');
    u.innerHTML = `<span class="dot"></span> atualizado ${d.updated_at||''}`;
  }catch(e){
    document.getElementById('updated').textContent = 'erro ao atualizar';
  }
  if(timer) clearTimeout(timer);
  timer = setTimeout(load, POLL*1000);
}

// ---- Reordenar por arrastar ----------------------------------------------
let dragId = null;
function setupDrag(){
  document.querySelectorAll('#grid .card').forEach(card=>{
    card.addEventListener('dragstart', e=>{ dragId=card.dataset.id; card.classList.add('dragging'); e.dataTransfer.effectAllowed='move'; });
    card.addEventListener('dragend', ()=>{ card.classList.remove('dragging'); document.querySelectorAll('.drop-target').forEach(c=>c.classList.remove('drop-target')); });
    card.addEventListener('dragover', e=>{ e.preventDefault(); if(card.dataset.id!==dragId) card.classList.add('drop-target'); });
    card.addEventListener('dragleave', ()=>card.classList.remove('drop-target'));
    card.addEventListener('drop', e=>{
      e.preventDefault(); card.classList.remove('drop-target');
      const from = LAST.findIndex(a=>a.id===dragId);
      const to = LAST.findIndex(a=>a.id===card.dataset.id);
      if(from<0||to<0||from===to) return;
      const [moved] = LAST.splice(from,1); LAST.splice(to,0,moved);
      renderCards();
      fetch('/api/accounts/reorder',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ids:LAST.map(a=>a.id)})});
    });
  });
}


let CURLOGIN = null; // {id, mode, opened}

function cancelCurrentLogin(){
  const cur = CURLOGIN;
  CURLOGIN = null;
  if(cur){ fetch('/api/login/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:cur.id})}); }
}

function showLoginRetry(message){
  resetLoginUI();
  CURLOGIN = null;
  setMsg(message || 'Falha no login. Tente novamente.','bad');
  document.getElementById('modal_actions').innerHTML = `<button class="primary" onclick="onType()">Tentar novamente</button>
    <button class="ghost" onclick="closeAdd()">Cancelar</button>`;
}

function isLoopbackHost(){
  const h = window.location.hostname.toLowerCase();
  return h === 'localhost' || h === '::1' || h.startsWith('127.');
}

function openAdd(){
  document.getElementById('overlay').classList.add('show');
  document.getElementById('modal_msg').textContent='';
  document.getElementById('f_label').value='';
  document.getElementById('f_key').value='';
  cancelCurrentLogin();
  onType();
}
function closeAdd(){
  document.getElementById('overlay').classList.remove('show');
  cancelCurrentLogin();
}

function setMsg(txt, kind){ const m=document.getElementById('modal_msg'); m.className='msg'+(kind?(' '+kind):''); m.innerHTML=txt||''; }

function onType(){
  const t = document.getElementById('f_type').value;
  const keyBox = document.getElementById('key_box');
  const cliBox = document.getElementById('cli_box');
  const loginBox = document.getElementById('login_box');
  const actions = document.getElementById('modal_actions');
  // reseta area de login
  cancelCurrentLogin(); loginBox.style.display='none';
  document.getElementById('login_step').innerHTML='';
  document.getElementById('login_link').innerHTML='';
  document.getElementById('code_box').style.display='none';
  document.getElementById('f_code').value='';
  setMsg('');

  if(t==='deepseek' || t==='openrouter'){
    keyBox.style.display='block';
    const links = t==='deepseek'
      ? 'Pegue em <a href="https://platform.deepseek.com/api_keys" target="_blank">platform.deepseek.com</a>'
      : 'Pegue em <a href="https://openrouter.ai/settings/keys" target="_blank">openrouter.ai/settings/keys</a>';
    cliBox.innerHTML = links + '. Para varias contas, adicione uma chave de cada vez.';
    actions.innerHTML = `<button class="primary" onclick="submitAdd()">Salvar</button>
      <button onclick="closeAdd()">Cancelar</button>`;
  } else {
    keyBox.style.display='none';
    const nome = t==='codex' ? 'Codex' : 'Claude';
    const codexRemoto = t==='codex' && !isLoopbackHost();
    cliBox.innerHTML = codexRemoto
      ? `O login <b>Neste PC</b> do Codex usa <code>localhost:1455</code> e so funciona quando o painel roda neste mesmo computador.<br>
        Como voce esta acessando por servidor/domínio, use <b>No celular (QR)</b>.`
      : `Como voce quer entrar na conta <b>${nome}</b>?<br>
        Para monitorar <b>2 contas</b>, adicione uma e depois abra este popup de novo para a outra.`;
    actions.innerHTML = codexRemoto ? `<div class="choice" style="width:100%">
        <button class="primary" onclick="startLogin('phone')">${svg('phone')} No celular (QR)</button>
      </div>
      <button class="ghost" onclick="closeAdd()">Cancelar</button>` : `<div class="choice" style="width:100%">
        <button class="primary" onclick="startLogin('browser')">${svg('monitor')} Neste PC</button>
        <button class="primary" onclick="startLogin('phone')">${svg('phone')} No celular (QR)</button>
      </div>
      <button class="ghost" onclick="captureCurrent()" title="Usa a conta que ja esta logada no computador">Usar login atual</button>
      <button class="ghost" onclick="closeAdd()">Cancelar</button>`;
  }
}

async function submitAdd(){
  const type = document.getElementById('f_type').value;
  const label = document.getElementById('f_label').value;
  const api_key = document.getElementById('f_key').value;
  setMsg('Salvando...');
  try{
    const r = await fetch('/api/accounts/add',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type,label,api_key})});
    const d = await r.json();
    if(d.ok){ setMsg('Conta adicionada!','ok'); setTimeout(()=>{closeAdd();load();},600); }
    else { setMsg(d.error||'Erro.','bad'); }
  }catch(e){ setMsg('Erro de conexao.','bad'); }
}

// "Usar login atual" = captura a conta ja logada no CLI (sem abrir navegador)
function captureCurrent(){ submitAdd(); }

function resetLoginUI(){
  document.getElementById('login_step').innerHTML='';
  document.getElementById('qrbox').innerHTML='';
  document.getElementById('devcode_box').style.display='none';
  document.getElementById('login_link').innerHTML='';
  document.getElementById('code_box').style.display='none';
  document.getElementById('f_code').value='';
}

function renderQR(text){
  const box = document.getElementById('qrbox');
  try{
    const qr = qrcode(0, 'L'); qr.addData(text); qr.make();
    box.innerHTML = qr.createSvgTag({cellSize:4, margin:1, scalable:true});
  }catch(e){ box.innerHTML = '<div class="hint">(nao consegui gerar o QR — use o link abaixo)</div>'; }
}

async function startLogin(method){
  const type = document.getElementById('f_type').value;
  const label = document.getElementById('f_label').value;
  if(type==='codex' && method==='browser' && !isLoopbackHost()){
    showLoginRetry("No deploy, o login 'Neste PC' do Codex nao funciona porque o retorno usa localhost:1455. Use 'No celular (QR)'.");
    return;
  }
  // Claude no PC: quem abre o navegador somos nos. Abrimos a aba JA no clique
  // (senao o bloqueador de popup barra). Codex abre a janela sozinho.
  let win = null;
  if(method==='browser' && type==='claude'){ win = window.open('', '_blank'); }
  resetLoginUI();
  setMsg('Iniciando login...');
  document.getElementById('login_box').style.display='block';
  document.getElementById('modal_actions').innerHTML = `<button onclick="closeAdd()">Cancelar</button>`;
  try{
    const r = await fetch('/api/login/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type,label,method,replace:true})});
    const d = await r.json();
    if(!d.ok){ if(win) win.close(); showLoginRetry(d.error||'Erro.'); return; }
    CURLOGIN={id:d.id, mode:d.mode, method:d.method, shown:false, win:win};
    setMsg('');
    pollLogin();
  }catch(e){ if(win) win.close(); showLoginRetry('Erro de conexao.'); }
}

async function pollLogin(){
  if(!CURLOGIN) return;
  let d;
  try{ d = await (await fetch('/api/login/status?id='+CURLOGIN.id)).json(); }
  catch(e){ setTimeout(pollLogin, 1500); return; }

  const step = document.getElementById('login_step');
  const linkDiv = document.getElementById('login_link');
  const phone = CURLOGIN.method==='phone';

  // quando a URL fica disponivel, monta a tela (uma vez)
  if(d.url && !CURLOGIN.shown){
    CURLOGIN.shown=true;
    if(phone){
      renderQR(d.url);
      linkDiv.innerHTML = `<a href="${d.url}" target="_blank">${svg('link',13)} abrir o link no próprio celular</a>`;
    } else {
      // no PC: para o Claude, direcionamos a aba que abrimos no clique (uma janela so).
      // para o Codex, o proprio programa ja abriu a janela.
      if(d.mode==='paste'){
        if(CURLOGIN.win){ try{ CURLOGIN.win.location = d.url; }catch(e){ window.open(d.url,'_blank'); } }
        else { window.open(d.url,'_blank'); }
      }
      linkDiv.innerHTML = `Se o navegador não abriu, clique aqui:<br>
        <a href="${d.url}" target="_blank">${svg('link',13)} Abrir página de login</a>`;
    }
  }

  // codigo de device (Codex no celular)
  if(d.mode==='device' && d.code){
    document.getElementById('devcode_box').style.display='block';
    document.getElementById('devcode').textContent = d.code;
  }

  if(d.status==='done'){
    resetLoginUI(); setMsg('Login concluído! ✓','ok'); CURLOGIN=null;
    setTimeout(()=>{closeAdd();load();}, 800); return;
  }
  if(d.status==='error'){ showLoginRetry(d.message||'Falha no login.'); return; }

  // textos de instrucao + campo de colar (Claude)
  if(d.mode==='paste'){
    step.innerHTML = phone
      ? '1) Escaneie o QR com o celular. &nbsp; 2) Faca login. &nbsp; 3) O celular vai mostrar um <b>codigo</b> — digite-o aqui embaixo.'
      : '1) Faca login na janela que abriu. &nbsp; 2) O site vai mostrar um <b>codigo</b>. &nbsp; 3) Cole o codigo aqui embaixo.';
    document.getElementById('code_box').style.display='block';
  } else if(d.mode==='device'){
    step.innerHTML = '1) Escaneie o QR (ou abra o link) no celular. &nbsp; 2) Digite o codigo mostrado acima. &nbsp; 3) Aguarde — conclui sozinho.';
  } else {
    step.innerHTML = 'Aguardando voce concluir o login na janela do navegador...';
  }
  setTimeout(pollLogin, 1500);
}

async function submitLoginCode(){
  if(!CURLOGIN) return;
  const code = document.getElementById('f_code').value;
  if(!code.trim()){ setMsg('Cole o codigo primeiro.','bad'); return; }
  setMsg('Validando codigo...');
  try{
    const r = await fetch('/api/login/code',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:CURLOGIN.id, code})});
    const d = await r.json();
    if(!d.ok){ setMsg(d.error||'Erro ao enviar codigo.','bad'); return; }
    setMsg('');
  }catch(e){ setMsg('Erro de conexao.','bad'); }
}

async function delAcc(id){
  if(!confirm('Remover esta conta do monitor?')) return;
  await fetch('/api/accounts/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  load();
}

// injeta os icones (logo + botoes) uma vez
document.getElementById('brandlogo').innerHTML = svg('activity',19);
document.getElementById('emptylogo').innerHTML = svg('activity',28);
document.getElementById('btnRefresh').insertAdjacentHTML('afterbegin', svg('refresh'));
document.getElementById('btnAdd').insertAdjacentHTML('afterbegin', svg('plus'));

// fecha o modal clicando fora
document.getElementById('overlay').addEventListener('click', e=>{ if(e.target.id==='overlay') closeAdd(); });

load();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Inicializacao
# --------------------------------------------------------------------------

def main():
    cfg = load_config()
    port = int(os.environ.get("IA_MONITOR_PORT", cfg.get("port", 8765)))
    host = LISTEN_HOST

    # Se a porta estiver ocupada, tenta as proximas
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print("Nao consegui abrir uma porta livre.")
        sys.exit(1)

    url = f"http://{host}:{port}/"
    print("=" * 52)
    print("  Monitor de IA rodando!")
    print("  Abra no navegador: " + url)
    print("  (Para fechar: feche esta janela ou aperte Ctrl+C)")
    print("=" * 52)

    # So abre o navegador sozinho no modo local. Em servidor (0.0.0.0) nao faz sentido.
    if host.startswith("127.") or host == "localhost":
        try:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
        server.shutdown()


if __name__ == "__main__":
    main()
