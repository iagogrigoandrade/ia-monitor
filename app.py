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
from collections import deque
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
import tempfile
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

def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "sim")


def env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except Exception:
        return default

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, "assets")
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

# Rate limit em memoria, por IP. Mantem o painel responsivo mesmo se alguem
# descobrir a senha e tentar martelar as APIs. Ajuste por variaveis de ambiente.
RATE_LIMIT_ENABLED = env_bool("IA_MONITOR_RATE_LIMIT", True)
RATE_LIMIT_TRUST_PROXY = env_bool("IA_MONITOR_TRUST_PROXY", False)
RATE_WINDOW = env_int("IA_MONITOR_RATE_WINDOW", 60)
RATE_GLOBAL = env_int("IA_MONITOR_RATE_GLOBAL", 240)
RATE_API = env_int("IA_MONITOR_RATE_API", 120)
RATE_STATUS = env_int("IA_MONITOR_RATE_STATUS", 60)
RATE_WRITE = env_int("IA_MONITOR_RATE_WRITE", 40)
RATE_LOGIN_START = env_int("IA_MONITOR_RATE_LOGIN_START", 10)
RATE_LOGIN_CODE = env_int("IA_MONITOR_RATE_LOGIN_CODE", 20)
RATE_AUTH_FAIL = env_int("IA_MONITOR_RATE_AUTH_FAIL", 8)
RATE_AUTH_FAIL_WINDOW = env_int("IA_MONITOR_RATE_AUTH_FAIL_WINDOW", 300)
MAX_JSON_BODY = env_int("IA_MONITOR_MAX_BODY_BYTES", 65536)

# Onde os CLIs guardam o login OAuth (mesmo em qualquer sistema operacional)
CODEX_AUTH = os.path.join(HOME, ".codex", "auth.json")
CODEX_INSTALLATION_ID = os.path.join(HOME, ".codex", "installation_id")
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
    """Grava de volta tokens renovados (rotacao de refresh token).

    Read-modify-write ATOMICO: segura o lock durante todo o ciclo para que
    renovacoes de contas diferentes em paralelo nao sobrescrevam umas as
    outras (o que perderia o refresh_token rotacionado e derrubaria o login).
    """
    with _config_lock:
        cfg = load_config()
        for acc in cfg["accounts"]:
            if acc.get("id") == account_id:
                acc.setdefault("creds", {}).update(new_creds)
                break
        save_config(cfg)


def current_account_creds(account_id):
    """Le as credenciais mais recentes da conta direto do config."""
    with _config_lock:
        for acc in load_config().get("accounts", []):
            if acc.get("id") == account_id:
                return dict(acc.get("creds", {}))
    return {}


# Serializa a renovacao de token por conta: evita que dois fetches simultaneos
# gastem (e invalidem) o mesmo refresh_token ao renovar ao mesmo tempo.
_refresh_locks = {}
_refresh_locks_guard = threading.Lock()


def account_refresh_lock(account_id):
    with _refresh_locks_guard:
        lk = _refresh_locks.get(account_id)
        if lk is None:
            lk = threading.Lock()
            _refresh_locks[account_id] = lk
        return lk


_rate_lock = threading.Lock()
_rate_buckets = {}  # "escopo:ip" -> deque[timestamps]
_rate_hits = 0


def rate_limit_hit(scope, client_ip, limit, window=RATE_WINDOW):
    """Registra uma requisicao e retorna (bloqueado, retry_after_segundos)."""
    if not RATE_LIMIT_ENABLED:
        return False, 0

    now = time.monotonic()
    cutoff = now - window
    key = f"{scope}:{client_ip or 'unknown'}"

    with _rate_lock:
        global _rate_hits
        _rate_hits += 1

        bucket = _rate_buckets.get(key)
        if bucket is None:
            bucket = deque()
            _rate_buckets[key] = bucket

        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            retry = int(window - (now - bucket[0])) + 1
            return True, max(1, retry)

        bucket.append(now)

        # Limpeza ocasional para nao acumular IPs antigos indefinidamente.
        if _rate_hits % 500 == 0:
            stale_cutoff = now - max(RATE_WINDOW, RATE_AUTH_FAIL_WINDOW)
            for old_key, old_bucket in list(_rate_buckets.items()):
                while old_bucket and old_bucket[0] <= stale_cutoff:
                    old_bucket.popleft()
                if not old_bucket:
                    _rate_buckets.pop(old_key, None)

    return False, 0


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
    with account_refresh_lock(account_id):
        # Outra thread pode ter renovado enquanto esperavamos o lock: reaproveita
        # um access_token novo e ainda valido em vez de gastar o refresh_token.
        latest = current_account_creds(account_id)
        lt = latest.get("accessToken")
        if lt and lt != creds.get("accessToken"):
            exp = latest.get("expiresAt")
            if not exp or time.time() * 1000 < (exp - 60000):
                creds.update(latest)
                return creds
        rt = latest.get("refreshToken") or creds.get("refreshToken")
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
        return {"error": "Sem login do Claude. Reconecte a conta.", "reauth": True}

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
    if status == 401 or status == 403:
        return {"error": "Login do Claude expirado. Reconecte a conta.", "reauth": True}
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
            "resetAt": five.get("resets_at"),
        })
    if seven:
        metrics.append({
            "label": "Limite semanal",
            "percent": round(float(seven.get("utilization") or 0), 1),
            "reset": fmt_reset(seven.get("resets_at")),
            "resetAt": seven.get("resets_at"),
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
    with account_refresh_lock(account_id):
        # Outra thread pode ter renovado enquanto esperavamos o lock:
        # se ja existe um access_token novo e valido, reaproveita (nao gasta o refresh_token).
        latest = current_account_creds(account_id)
        lt = latest.get("access_token")
        if lt and lt != creds.get("access_token"):
            exp = jwt_exp(lt)
            if not exp or time.time() < (exp - 60):
                creds.update(latest)
                return creds
        rt = latest.get("refresh_token") or creds.get("refresh_token")
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


def _codex_home_path(home, filename):
    return os.path.join(home or HOME, ".codex", filename)


def _read_codex_install_id(home=None):
    try:
        p = _codex_home_path(home, "installation_id") if home else CODEX_INSTALLATION_ID
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def _codex_install_id(creds=None):
    iid = (creds or {}).get("installation_id")
    if iid:
        return str(iid).strip()
    return _read_codex_install_id()


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
    iid = _codex_install_id(creds)
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
    return {"label": _codex_window_label(label, win), "percent": percent,
            "reset": fmt_reset(reset), "resetAt": reset}


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
        return {"error": "Sem login do Codex. Reconecte a conta.", "reauth": True}

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
        return {"error": "Login do Codex expirado. Reconecte a conta.", "reauth": True}
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

    def set_updated(ts):
        base["updatedAt"] = int(ts * 1000)

    def with_cache_or(msg):
        """Mostra o ultimo valor bom (marcado) ou, se nao houver, a mensagem."""
        if cached and now - cached["ts"] < STALE_MAX:
            keep = dict(cached["result"])
            keep["stale"] = msg
            base.update(keep)
            set_updated(cached["ts"])
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
        set_updated(cached["ts"])
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
        out = with_cache_or(result["error"])
        # login expirado: sinaliza o botao de reconectar (so quando nao ha cache bom)
        if result.get("reauth") and out.get("error"):
            out["reauth"] = True
        return out

    base.update(result)
    set_updated(now)
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

def capture_codex(home=None):
    auth_path = _codex_home_path(home, "auth.json") if home else CODEX_AUTH
    if not os.path.exists(auth_path):
        return None, "Nao encontrei o login do Codex. Rode 'codex' e faca login primeiro."
    try:
        with open(auth_path, "r", encoding="utf-8") as f:
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
        iid = _read_codex_install_id(home)
        if home and not iid:
            iid = str(uuid.uuid4())
        if iid:
            creds["installation_id"] = iid
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
# Apos um 429 no device-auth do Codex, segura novos pedidos ate este instante
# (o OpenAI limita bastante o endpoint de device code).
_codex_device_cooldown_until = 0.0


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
    def __init__(self, provider, label, method, target_id=None):
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.label = label
        self.method = method            # "browser" | "phone"
        self.target_id = target_id      # reconexao: conta existente a atualizar
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
        self.codex_home = None
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
    with _config_lock:
        cfg = load_config()
        cfg["accounts"].append(acc)
        save_config(cfg)
    return acc["id"]


def _apply_login(provider, label, creds, target_id=None):
    """Reconexao: atualiza as credenciais de uma conta existente.
    Sem alvo (ou alvo inexistente/de outro tipo), cria uma conta nova."""
    if target_id:
        with _config_lock:
            cfg = load_config()
            for acc in cfg["accounts"]:
                if acc.get("id") == target_id and acc.get("type") == provider:
                    acc["creds"] = creds
                    if label:
                        acc["label"] = label
                    save_config(cfg)
                    _result_cache.pop(target_id, None)
                    _backoff.pop(target_id, None)
                    return target_id
    return _save_account(provider, label, creds)


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
    global _codex_device_cooldown_until
    exe = shutil.which("codex")
    if not exe:
        job.status = "error"
        job.message = "Nao encontrei o programa 'codex'. Instale o CLI do Codex primeiro."
        return
    args = ["login", "--device-auth"] if job.method == "phone" else ["login"]
    work_home = None
    try:
        # Isola cada login do Codex. O CLI oficial guarda apenas um auth.json por
        # HOME; compartilhar ~/.codex entre contas pode invalidar a sessao anterior.
        work_home = tempfile.mkdtemp(prefix="ia-monitor-codex-")
        job.codex_home = work_home
        codex_dir = os.path.join(work_home, ".codex")
        os.makedirs(codex_dir, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = work_home
        env["USERPROFILE"] = work_home
        env["CODEX_HOME"] = codex_dir
        env["XDG_CONFIG_HOME"] = os.path.join(work_home, ".config")
        drive, tail = os.path.splitdrive(work_home)
        if drive:
            env["HOMEDRIVE"] = drive
            env["HOMEPATH"] = tail or os.sep
        job.proc = subprocess.Popen(
            [exe] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env,
        )
    except Exception as e:
        job.status = "error"
        job.message = f"Falha ao iniciar login: {e}"
        if work_home:
            shutil.rmtree(work_home, ignore_errors=True)
            job.codex_home = None
        return

    try:
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
            creds, err = capture_codex(work_home)
            if err:
                job.status = "error"
                job.message = err
                return
            job.account_id = _apply_login("codex", job.label, creds, job.target_id)
            job.status = "done"
            job.message = "Login concluido!"
        else:
            tail = " ".join(job.lines[-4:]) if job.lines else ""
            job.status = "error"
            low = tail.lower()
            if "429" in low or "too many requests" in low:
                # OpenAI limitou o endpoint de device code: segura novas tentativas por um tempo.
                _codex_device_cooldown_until = time.time() + 120
                job.message = ("O OpenAI limitou os pedidos de login do Codex (429). "
                               "Aguarde 1-2 minutos e tente de novo.")
            else:
                job.message = f"Login nao concluido (codigo {rc}). {tail}".strip()
    finally:
        if work_home:
            shutil.rmtree(work_home, ignore_errors=True)
            job.codex_home = None


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
        job.account_id = _apply_login("claude", job.label, creds, job.target_id)
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


def start_login(provider, label, method, replace=False, target_id=None):
    if provider not in ("codex", "claude"):
        return None, "Login nao disponivel para este servico."
    if method not in ("browser", "phone"):
        method = "browser"
    with _login_lock:
        _refresh_login_jobs()
        active = [j for j in _login_jobs.values() if j.status in LOGIN_ACTIVE]
        # Reaproveita um login identico ja em andamento (mesmo provedor/metodo/alvo):
        # evita disparar um novo device-auth do Codex e tomar 429 do OpenAI a cada clique.
        for j in active:
            if j.provider == provider and j.method == method and j.target_id == target_id \
                    and (time.time() - j.started) < 300:
                return j, None
        # Respeita o cooldown apos um 429 no device-auth do Codex.
        if provider == "codex" and method == "phone":
            wait = int(_codex_device_cooldown_until - time.time())
            if wait > 0:
                return None, ("O OpenAI limitou os pedidos de login do Codex (429). "
                              f"Aguarde ~{wait}s e tente de novo.")
        if active and not replace:
            return None, "Ja existe um login em andamento. Conclua ou cancele antes."
        for j in active:
            _cancel_login_job(j, "Cancelado por nova tentativa.")
        job = LoginJob(provider, label, method, target_id)
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

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, str(value))
        self.end_headers()
        self.wfile.write(data)

    def _client_ip(self):
        if RATE_LIMIT_TRUST_PROXY:
            xff = self.headers.get("X-Forwarded-For", "")
            if xff:
                return xff.split(",", 1)[0].strip()
            real = self.headers.get("X-Real-IP", "")
            if real:
                return real.strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _send_rate_limited(self, retry_after):
        self._send(
            429,
            {"error": f"Muitas requisicoes. Tente de novo em {retry_after}s."},
            headers={"Retry-After": retry_after},
        )

    def _rate_limit(self, scope, limit, window=RATE_WINDOW):
        blocked, retry_after = rate_limit_hit(scope, self._client_ip(), limit, window)
        if blocked:
            self._send_rate_limited(retry_after)
            return False
        return True

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _send_asset(self, path):
        rel = path[len("/assets/"):].replace("/", os.sep)
        full = os.path.normpath(os.path.join(ASSET_DIR, rel))
        root = os.path.abspath(ASSET_DIR)
        if not full.startswith(root + os.sep) or not full.lower().endswith(".webp"):
            self._send(404, {"error": "not found"})
            return
        if not os.path.exists(full):
            self._send(404, {"error": "not found"})
            return
        with open(full, "rb") as f:
            self._send(200, f.read(), "image/webp")

    def _check_auth(self):
        """Exige usuario+senha se IA_MONITOR_PASSWORD estiver definida.
        Sem senha configurada (modo local), libera tudo."""
        if not AUTH_PASSWORD:
            return True
        ip = self._client_ip()
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
        blocked, retry_after = rate_limit_hit("auth-fail", ip, RATE_AUTH_FAIL, RATE_AUTH_FAIL_WINDOW)
        if blocked:
            self._send_rate_limited(retry_after)
            return False
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
        if not self._rate_limit("global", RATE_GLOBAL):
            return
        if not self._check_auth():
            return
        if path.startswith("/api/") and not self._rate_limit("api", RATE_API):
            return
        query = parse_qs(urlparse(self.path).query)
        if path == "/" or path.startswith("/index"):
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
        elif path == "/api/status":
            if not self._rate_limit("api-status", RATE_STATUS):
                return
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
        elif path.startswith("/assets/"):
            self._send_asset(path)
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
        path = urlparse(self.path).path
        if not self._rate_limit("global", RATE_GLOBAL):
            return
        if not self._check_auth():
            return
        if path.startswith("/api/") and not self._rate_limit("api", RATE_API):
            return
        if path.startswith("/api/") and not self._rate_limit("api-write", RATE_WRITE):
            return
        if path == "/api/login/start" and not self._rate_limit("login-start", RATE_LOGIN_START):
            return
        if path == "/api/login/code" and not self._rate_limit("login-code", RATE_LOGIN_CODE):
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except Exception:
            length = 0
        if length > MAX_JSON_BODY:
            self._send(413, {"error": "Requisicao grande demais."})
            return
        body = self._read_json()
        if path == "/api/accounts/add":
            self._handle_add(body)
        elif path == "/api/accounts/delete":
            self._handle_delete(body)
        elif path == "/api/accounts/rename":
            self._handle_rename(body)
        elif path == "/api/accounts/reorder":
            self._handle_reorder(body)
        elif path == "/api/settings":
            with _config_lock:
                cfg = load_config()
                if "refresh_seconds" in body:
                    try:
                        cfg["refresh_seconds"] = max(15, int(body["refresh_seconds"]))
                    except Exception:
                        pass
                save_config(cfg)
            self._send(200, {"ok": True})
        elif path == "/api/login/start":
            if body.get("type") == "codex" and (body.get("method") or "browser") == "browser" and \
                    not is_loopback_host(self.headers.get("Host", "")):
                self._send(400, {"error": "No deploy, o login 'Neste PC' do Codex nao funciona porque o retorno usa localhost:1455. Use 'No celular (QR)'."})
                return
            job, err = start_login(body.get("type"), (body.get("label") or "").strip(),
                                   body.get("method") or "browser", bool(body.get("replace")),
                                   (body.get("target_id") or "").strip() or None)
            if err:
                self._send(400, {"error": err})
            else:
                self._send(200, {"ok": True, "id": job.id, "mode": job.mode,
                                 "method": job.method})
        elif path == "/api/login/code":
            err = login_submit_code(body.get("id"), body.get("code"))
            self._send(200 if not err else 400, {"ok": not err, "error": err})
        elif path == "/api/login/cancel":
            login_cancel(body.get("id"))
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def _handle_add(self, body):
        atype = body.get("type")
        label = (body.get("label") or "").strip()
        target_id = (body.get("target_id") or "").strip() or None
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

        with _config_lock:
            cfg = load_config()
            # reconexao: atualiza a conta existente em vez de duplicar
            existing = next((a for a in cfg["accounts"]
                             if target_id and a.get("id") == target_id and a.get("type") == atype), None)
            if existing:
                if label:
                    existing["label"] = label
                for k in ("api_key", "creds"):
                    if k in acc:
                        existing[k] = acc[k]
                save_config(cfg)
                _result_cache.pop(existing["id"], None)
                _backoff.pop(existing["id"], None)
                self._send(200, {"ok": True, "id": existing["id"]})
                return
            cfg["accounts"].append(acc)
            save_config(cfg)
        self._send(200, {"ok": True, "id": acc["id"]})

    def _handle_delete(self, body):
        aid = body.get("id")
        with _config_lock:
            cfg = load_config()
            before = len(cfg["accounts"])
            cfg["accounts"] = [a for a in cfg["accounts"] if a.get("id") != aid]
            save_config(cfg)
        _result_cache.pop(aid, None)
        self._send(200, {"ok": True, "removed": before - len(cfg["accounts"])})

    def _handle_rename(self, body):
        aid = body.get("id")
        label = (body.get("label") or "").strip()[:80]
        with _config_lock:
            cfg = load_config()
            for acc in cfg["accounts"]:
                if acc.get("id") == aid:
                    acc["label"] = label or TYPE_LABELS.get(acc.get("type"), "Conta")
                    save_config(cfg)
                    self._send(200, {"ok": True, "label": acc["label"]})
                    return
        self._send(404, {"error": "Conta nao encontrada."})

    def _handle_reorder(self, body):
        ids = body.get("ids") or []
        with _config_lock:
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Monitor de IA</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script>
  // aplica o tema salvo antes do render, evitando o flash
  (function(){ try{ document.documentElement.setAttribute('data-theme', localStorage.getItem('ia-theme') || 'dark'); }catch(e){ document.documentElement.setAttribute('data-theme','dark'); } })();
</script>
<style>
  :root{
    --bg:#1B2438; --bg2:#151d30; --surface:#222e49; --surface2:#2b3a5c;
    --line:#38466385; --line2:#465575;
    --txt:#F8FAFC; --muted:#a6b3c9; --faint:#7c8aa5;
    --accent:#22C55E; --accent-ink:#052e16;
    --ok:#22C55E; --warn:#F5A623; --bad:#EF4444;
    --claude:#d97757; --codex:#10b981; --deepseek:#4f8cff; --openrouter:#a855f7;
    --r:14px; --r-sm:9px; --sp:8px;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -12px rgba(0,0,0,.7);
    --ease:cubic-bezier(.2,.7,.3,1);
  }
  html[data-theme="light"]{
    --bg:#eef2f8; --bg2:#ffffff; --surface:#ffffff; --surface2:#f1f5f9;
    --line:#e2e8f0; --line2:#cbd5e1;
    --txt:#0f172a; --muted:#475569; --faint:#64748b;
    --accent:#16a34a; --accent-ink:#ffffff;
    --shadow:0 1px 2px rgba(15,23,42,.06), 0 8px 24px -12px rgba(15,23,42,.18);
  }
  html[data-theme="light"] body{
    background:radial-gradient(1200px 600px at 80% -10%, #dbe4f3 0%, var(--bg) 55%) fixed}
  html[data-theme="light"] header{background:rgba(255,255,255,.82)}
  html[data-theme="light"] .brand .logo{background:linear-gradient(145deg,#ffffff,#eef2f8)}
  html[data-theme="light"] .bar{background:#e2e8f0}
  html[data-theme="light"] button.danger{color:#dc2626;border-color:#fecaca}
  html[data-theme="light"] .err{color:#b91c1c;border-color:#fecaca}
  body{transition:background-color .25s var(--ease),color .25s var(--ease)}
  .card,button,input,select,.modal,header{transition:background-color .25s var(--ease),border-color .25s var(--ease),color .25s var(--ease)}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;font-family:"Fira Sans","Segoe UI",Roboto,system-ui,sans-serif;
    background:radial-gradient(1200px 600px at 80% -10%, #2a3757 0%, var(--bg) 55%) fixed;
    color:var(--txt);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}
  .mono{font-family:"Fira Code",ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}

  header{display:flex;align-items:center;flex-wrap:wrap;gap:10px 14px;
    padding:max(12px,env(safe-area-inset-top)) max(clamp(12px,4vw,28px),env(safe-area-inset-right)) 12px max(clamp(12px,4vw,28px),env(safe-area-inset-left));
    border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;
    background:rgba(27,36,56,.82);backdrop-filter:saturate(140%) blur(10px)}
  .brand{display:flex;align-items:center;gap:11px}
  .brand .logo{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;
    background:linear-gradient(145deg,#2c3a58,#1a233a);border:1px solid var(--line2);color:var(--accent)}
  .brand h1{font-size:17px;margin:0;font-weight:600;letter-spacing:.2px}
  .brand .sub{font-size:11px;color:var(--faint);margin-top:1px}
  header .spacer{flex:1}
  header .btns{display:flex;align-items:center;gap:8px;flex-wrap:wrap}

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
    gap:16px;padding:clamp(12px,4vw,28px);max-width:1400px;margin:0 auto;align-items:stretch;
    padding-bottom:max(clamp(12px,4vw,28px),calc(env(safe-area-inset-bottom) + 12px));
    padding-left:max(clamp(12px,4vw,28px),env(safe-area-inset-left));
    padding-right:max(clamp(12px,4vw,28px),env(safe-area-inset-right))}
  .card{background:linear-gradient(180deg,var(--surface),var(--bg2));border:1px solid var(--line);
    border-radius:var(--r);padding:18px 18px 14px;position:relative;box-shadow:var(--shadow);
    display:flex;flex-direction:column;min-height:250px;
    transition:border-color .18s var(--ease),transform .18s var(--ease),box-shadow .18s var(--ease)}
  .card:hover{border-color:var(--line2)}
  .card.dragging{opacity:.4}
  .card.drop-target{border-color:var(--accent);border-style:dashed}
  .card .corner{position:absolute;top:11px;right:11px;display:flex;align-items:center;gap:6px;z-index:1}
  /* nos cards com botao de creditos, alinha o canto com o icone do provedor */
  .card.has-topup .corner{top:24px}
  .card .grip{color:var(--faint);cursor:grab;display:flex;gap:2px;padding:4px;border-radius:6px}
  .card .grip:hover{color:var(--muted);background:var(--surface2)}
  .card .grip:active{cursor:grabbing}
  .card .top{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding-right:22px}
  .card.has-topup .top{padding-right:96px}
  .ic{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;flex-shrink:0;
    overflow:hidden;background:#fff;border:1px solid var(--line2);box-shadow:0 6px 18px -14px rgba(0,0,0,.9)}
  .ic img{width:100%;height:100%;object-fit:cover;display:block}
  .ic svg{width:18px;height:18px}
  .i-claude{background:rgba(217,119,87,.14);color:var(--claude)}
  .i-codex{background:rgba(16,185,129,.14);color:var(--codex)}
  .i-deepseek{background:rgba(79,140,255,.14);color:var(--deepseek)}
  .i-openrouter{background:rgba(168,85,247,.14);color:var(--openrouter)}
  .card h3{margin:0;font-size:15px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .card .badge{font-size:10.5px;color:var(--muted);font-weight:500;letter-spacing:.3px;text-transform:uppercase}
  .card .detail{font-size:11.5px;color:var(--faint);margin:-6px 0 12px 48px;word-break:break-all}

  .metric{margin:14px 0}
  .metric .row{display:flex;justify-content:space-between;align-items:baseline;font-size:13px;margin-bottom:6px}
  .metric .row .lbl{color:var(--muted)}
  .metric .row .pct{font-weight:600}
  .metric .row .r{color:var(--faint);font-size:11px;font-weight:400}
  .metric .usage{font-size:14px;margin:-1px 0 7px;color:var(--muted)}
  .metric .usage .pct{font-size:18px;font-weight:700;color:var(--txt);margin-right:4px}
  .metric .usage .r{font-size:12.5px;color:var(--muted)}
  .bar{height:8px;background:#141d31;border-radius:20px;overflow:hidden;border:1px solid var(--line)}
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
    border-radius:var(--r);padding:24px;width:min(520px,96vw);max-height:92vh;max-height:92dvh;
    overflow:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;
    box-shadow:0 24px 60px -20px rgba(0,0,0,.8);animation:pop .22s var(--ease)}
  @keyframes pop{from{opacity:0;transform:scale(.97) translateY(8px)}to{opacity:1;transform:none}}
  .modal h2{margin:0 0 4px;font-size:19px;font-weight:600;letter-spacing:-.2px}
  .modal p.sub{margin:0;color:var(--muted);font-size:13px;line-height:1.5}
  .modal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
  .modal-x{width:34px;height:34px;padding:0;border-radius:9px;background:transparent;border:1px solid transparent;
    color:var(--faint);display:grid;place-items:center;flex-shrink:0}
  .modal-x:hover{color:var(--txt);border-color:var(--line2);background:var(--surface2);transform:none}
  .modal-x svg{width:18px;height:18px}
  label{display:block;font-size:12.5px;margin:16px 0 6px;color:var(--muted);font-weight:500}
  input,select{width:100%;padding:11px;background:var(--bg2);border:1px solid var(--line2);
    border-radius:var(--r-sm);color:var(--txt);font-size:14px;font-family:inherit;transition:border-color .15s}
  input:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(34,197,94,.15)}
  input::placeholder{color:var(--faint)}
  .field{margin-top:18px}
  .flabel{display:block;font-size:12px;margin:0 0 9px;color:var(--muted);font-weight:600;
    letter-spacing:.3px;text-transform:uppercase}
  .svc-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .svc{display:flex;flex-direction:column;align-items:flex-start;gap:2px;text-align:left;
    padding:13px 14px;border-radius:var(--r-sm);border:1px solid var(--line2);background:var(--bg2);
    cursor:pointer;font-weight:400;
    transition:border-color .16s var(--ease),background-color .16s var(--ease),box-shadow .16s var(--ease),transform .16s var(--ease)}
  .svc:hover{border-color:var(--faint);transform:translateY(-1px)}
  .svc img{width:26px;height:26px;border-radius:7px;margin-bottom:6px;object-fit:contain;background:#fff;padding:2px}
  .svc-name{font-size:14px;font-weight:600;color:var(--txt)}
  .svc-desc{font-size:11.5px;color:var(--faint);font-weight:400}
  .svc.active{border-color:var(--accent);background:rgba(34,197,94,.1);box-shadow:0 0 0 3px rgba(34,197,94,.16)}
  .svc.active .svc-desc{color:var(--muted)}
  .svc:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .svc:active{transform:scale(.985)}
  .svc-grid.locked .svc{pointer-events:none;opacity:.45}
  .svc-grid.locked .svc.active{opacity:1}
  .card .reconnect{width:100%;justify-content:center;margin-top:10px}
  .card .topup{display:inline-flex;align-items:center;gap:5px;padding:5px 9px;border-radius:7px;
    border:1px solid rgba(34,197,94,.35);background:rgba(34,197,94,.1);color:var(--accent);
    font-size:11.5px;font-weight:500;line-height:1;text-decoration:none;white-space:nowrap;
    transition:border-color .16s var(--ease),background-color .16s var(--ease)}
  .card .topup:hover{border-color:var(--accent);background:rgba(34,197,94,.18);color:var(--accent);text-decoration:none}
  .card .topup svg{width:14px;height:14px}
  .hint{font-size:12.5px;color:var(--muted);margin-top:14px;line-height:1.6}
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
    button{padding:9px 13px;font-size:12.5px}
    .grid{grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
    .card{min-height:210px}
  }
  /* ---- celular (bottom sheet + alvos de toque maiores) ------------------- */
  @media (max-width:600px){
    /* header numa linha so: "Monitor de IA" a esquerda, so os icones a direita */
    header{gap:8px;flex-wrap:nowrap}
    header .spacer{display:block;flex:1}
    .brand{min-width:0}
    .brand h1{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    header .btns{gap:8px;flex:0 0 auto}
    .btn-label{display:none}
    header .btns button{width:44px;height:44px;min-height:44px;padding:0;justify-content:center;flex:0 0 auto;gap:0}
    header .btns button svg{width:18px;height:18px;margin:0}
    .grid{grid-template-columns:1fr;gap:12px}
    .card{padding:15px 15px 11px;min-height:auto;border-radius:14px}
    .card.has-topup .top{padding-right:110px}
    .card h3{font-size:14.5px}
    .card .detail{font-size:11px;margin:-4px 0 10px 48px}
    .val{font-size:22px}
    .val small{font-size:11.5px}
    .metric{margin:11px 0}
    .metric .row{font-size:12.5px}
    .cfoot{padding-top:10px}
    .iconbtn{width:44px;height:44px}
    .cfoot .acts{gap:2px}

    /* modal vira folha inferior (bottom sheet): mais ergonomico no polegar */
    .overlay{align-items:flex-end;padding:0}
    .modal{width:100%;max-width:none;border-radius:20px 20px 0 0;padding:20px 18px;
      max-height:94dvh;animation:sheet .3s var(--ease)}
    .modal::before{content:"";display:block;width:40px;height:4px;border-radius:99px;
      background:var(--line2);margin:-6px auto 14px}
    .modal{padding-bottom:calc(20px + env(safe-area-inset-bottom))}
    .modal h2{font-size:18px}
    .modal-x{width:40px;height:40px}
    label{font-size:12px;margin:12px 0 4px}
    input,select{padding:12px;font-size:16px;min-height:46px}
    .svc{padding:14px}
    .svc-grid{gap:8px}
    .devcode{font-size:24px;letter-spacing:3px;padding:12px}
    #qrbox svg{width:180px;height:180px}
    .choice button{padding:15px 12px;min-width:120px;font-size:13px}
    .modal .actions button,.modal .actions>.primary{min-height:46px}
    .empty{padding:44px 18px}
    .empty h2{font-size:16px}
  }
  @keyframes sheet{from{opacity:.4;transform:translateY(100%)}to{opacity:1;transform:none}}
  /* ---- celular pequeno --------------------------------------------------- */
  @media (max-width:360px){
    .card h3{font-size:13.5px}
    .val{font-size:20px}
    .devcode{font-size:21px;letter-spacing:2px}
    #qrbox svg{width:160px;height:160px}
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
    <button class="ghost" onclick="toggleTheme()" id="btnTheme" title="Alternar tema claro/escuro" aria-label="Alternar tema"></button>
    <button class="ghost" onclick="load(true)" id="btnRefresh" title="Atualizar agora" aria-label="Atualizar agora"><span class="btn-label">Atualizar agora</span></button>
    <button class="primary" onclick="openAdd()" id="btnAdd" title="Adicionar conta" aria-label="Adicionar conta"><span class="btn-label">Adicionar conta</span></button>
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
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal_title">
    <div class="modal-head">
      <div>
        <h2 id="modal_title">Adicionar conta</h2>
        <p class="sub">Conecte um serviço para acompanhar uso e créditos em um só painel.</p>
      </div>
      <button class="modal-x" onclick="closeAdd()" aria-label="Fechar" title="Fechar" id="modal_close"></button>
    </div>

    <input type="hidden" id="f_type" value="claude">
    <div class="field">
      <span class="flabel">Serviço</span>
      <div class="svc-grid" id="svc_grid" role="radiogroup" aria-label="Serviço">
        <button type="button" class="svc" data-type="claude" role="radio" onclick="selectType('claude')">
          <img src="/assets/integrations/claude-icon.webp" alt="" aria-hidden="true" decoding="async">
          <span class="svc-name">Claude</span>
          <span class="svc-desc">Limite 5h e semanal</span>
        </button>
        <button type="button" class="svc" data-type="codex" role="radio" onclick="selectType('codex')">
          <img src="/assets/integrations/codex-icon.webp" alt="" aria-hidden="true" decoding="async">
          <span class="svc-name">Codex</span>
          <span class="svc-desc">ChatGPT · 5h e semanal</span>
        </button>
        <button type="button" class="svc" data-type="deepseek" role="radio" onclick="selectType('deepseek')">
          <img src="/assets/integrations/deepseek-icon.webp" alt="" aria-hidden="true" decoding="async">
          <span class="svc-name">DeepSeek</span>
          <span class="svc-desc">Saldo da API</span>
        </button>
        <button type="button" class="svc" data-type="openrouter" role="radio" onclick="selectType('openrouter')">
          <img src="/assets/integrations/openrouter-icon.webp" alt="" aria-hidden="true" decoding="async">
          <span class="svc-name">OpenRouter</span>
          <span class="svc-desc">Créditos da conta</span>
        </button>
      </div>
    </div>

    <div class="field">
      <label class="flabel" for="f_label">Nome da conta</label>
      <input id="f_label" placeholder="Ex.: Codex — Conta principal" autocomplete="off">
    </div>

    <div id="key_box" class="field">
      <label class="flabel" for="f_key">API Key</label>
      <input id="f_key" placeholder="cole a chave aqui" autocomplete="off">
    </div>

    <div id="cli_box" class="hint"></div>

    <div id="login_box" style="display:none">
      <div id="login_step" class="steps"></div>
      <div id="qrbox"></div>
      <div id="devcode_box" style="display:none">
        <div class="hint" style="text-align:center">Digite este codigo na pagina que abrir:</div>
        <div class="devcode" id="devcode"></div>
        <button class="primary" id="copy_devcode" style="width:100%;justify-content:center" onclick="copyDevCode()">Copiar código</button>
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
let RECONNECT_ID = null; // id da conta em reconexao (null = adicionar nova)

// ---- Icones SVG (Lucide) — sem emojis ------------------------------------
const ICON = {
  activity:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  refresh:'<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  trash:'<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  edit:'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>',
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
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
};
function svg(name, w){ return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${w?` style="width:${w}px;height:${w}px"`:''}>${ICON[name]||''}</svg>`; }

function applyTheme(t){
  t = (t === 'light') ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  try{ localStorage.setItem('ia-theme', t); }catch(e){}
  const btn = document.getElementById('btnTheme');
  if(btn) btn.innerHTML = svg(t === 'light' ? 'moon' : 'sun');
}
function toggleTheme(){
  const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  applyTheme(cur === 'light' ? 'dark' : 'light');
}
const TYPEICON = {claude:'bot', codex:'cpu', deepseek:'activity', openrouter:'link'};
const TYPELOGO = {
  claude:'/assets/integrations/claude-icon.webp',
  codex:'/assets/integrations/codex-icon.webp',
  deepseek:'/assets/integrations/deepseek-icon.webp',
  openrouter:'/assets/integrations/openrouter-icon.webp',
};
// paginas de recarga de credito (contas por saldo/creditos)
const TOPUP = {
  deepseek:'https://platform.deepseek.com/top_up',
  openrouter:'https://openrouter.ai/settings/credits',
};

function providerMark(type){
  const src = TYPELOGO[type];
  return src ? `<img src="${src}" alt="" aria-hidden="true" loading="lazy" decoding="async">` : svg(TYPEICON[type]||'activity');
}

function color(p){ if(p>=90) return 'var(--bad)'; if(p>=70) return 'var(--warn)'; return 'var(--ok)'; }
function fmtNum(v){ return (typeof v==='number') ? v.toLocaleString('pt-BR',{maximumFractionDigits:4}) : v; }
function esc(v){ return String(v ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function metricLabel(label){ return label === 'Limite semanal' ? 'Semanal' : label; }

function resetDate(value){
  if(value === undefined || value === null || value === '') return null;
  if(typeof value === 'number') return new Date(value < 1e12 ? value * 1000 : value);
  const raw = String(value).trim();
  if(/^\d+(\.\d+)?$/.test(raw)){
    const n = Number(raw);
    return new Date(n < 1e12 ? n * 1000 : n);
  }
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

function resetRelative(date){
  const secs = Math.floor((date.getTime() - Date.now()) / 1000);
  if(secs <= 0) return 'agora';
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if(d > 0) return `em ${d}d ${h}h`;
  if(h > 0) return `em ${h}h ${m}m`;
  return `em ${m}m`;
}

function resetAbsolute(date){
  const mins = Math.floor((date.getTime() - Date.now()) / 60000);
  if(mins < 24 * 60){
    return date.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  }
  return date.toLocaleString('pt-BR',{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).replace(',', '');
}

function resetText(m){
  const d = resetDate(m.resetAt);
  if(!d) return m.reset || '';
  return `${resetRelative(d)} (${resetAbsolute(d)})`;
}

function updatedAgo(value){
  const d = resetDate(value);
  if(!d) return 'ainda não atualizado';
  const mins = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  if(mins < 1) return 'atualizado agora';
  if(mins === 1) return 'atualizado há 1 min';
  return `atualizado há ${mins} min`;
}

function metricHTML(m){
  if(m.percent !== undefined && m.percent !== null){
    const p = Math.max(0, Math.min(100, m.percent));
    const reset = resetText(m);
    return `<div class="metric">
      <div class="row"><span class="lbl">${esc(metricLabel(m.label))}</span></div>
      <div class="usage"><span class="pct mono">${esc(m.percent)}%</span>${reset?`<span class="r">${esc(reset)}</span>`:''}</div>
      <div class="bar"><span style="width:${p}%;background:${color(p)}"></span></div>
    </div>`;
  }
  if(m.value !== undefined && m.value !== null){
    return `<div class="metric">
      <div class="row"><span class="lbl">${esc(m.label)}</span></div>
      <div class="val mono">${esc(fmtNum(m.value))}<small>${esc(m.unit||'')}</small></div>
    </div>`;
  }
  return '';
}

function cardHTML(a){
  let inner;
  if(a.error){
    inner = `<div class="err">${esc(a.error)}</div>`;
    if(a.reauth){
      inner += `<button class="primary reconnect" onclick="reconnect('${a.id}')">${svg('refresh')} Reconectar</button>`;
    }
  } else {
    inner = (a.metrics||[]).map(metricHTML).join('') || '<div class="err">Sem dados.</div>';
    if(a.stale){ inner += `<div class="stale">${svg('clock',13)} mostrando o último valor — atualizando…</div>`; }
  }
  const foot = `<div class="cfoot">
       <span class="auto" title="Última atualização deste card">${svg('clock',13)} ${esc(updatedAgo(a.updatedAt))}</span>
       <span class="acts">
          <button class="iconbtn" title="Renomear conta" onclick="renameAcc('${a.id}')">${svg('edit')}</button>
          <button class="iconbtn danger" title="Remover conta" onclick="delAcc('${a.id}')">${svg('trash')}</button>
        </span>
      </div>`;
  const topup = TOPUP[a.type]
    ? `<a class="topup" href="${TOPUP[a.type]}" target="_blank" rel="noopener" title="Adicionar créditos">${svg('wallet',15)} Créditos</a>`
    : '';
  return `<div class="card${topup?' has-topup':''}" draggable="true" data-id="${a.id}">
    <div class="corner">
      ${topup}
      <span class="grip" title="Arraste para reordenar">${svg('grip')}</span>
    </div>
    <div class="top">
      <span class="ic i-${a.type}">${providerMark(a.type)}</span>
      <div style="min-width:0">
        <h3>${esc(a.label||a.typeLabel)}</h3>
        <span class="badge">${esc(a.typeLabel)}</span>
      </div>
    </div>
    ${a.detail?`<div class="detail">${esc(a.detail)}</div>`:''}
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
  }catch(e){
    console.error('erro ao atualizar', e);
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
  document.getElementById('modal_actions').innerHTML = `<button class="primary" onclick="onType()">Tentar novamente</button>`;
}

function isLoopbackHost(){
  const h = window.location.hostname.toLowerCase();
  return h === 'localhost' || h === '::1' || h.startsWith('127.');
}
function isMobileDevice(){
  return /Android|iPhone|iPad|iPod|Mobile|Opera Mini|IEMobile/i.test(navigator.userAgent || '');
}

function selectType(t){
  document.getElementById('f_type').value = t;
  document.querySelectorAll('#svc_grid .svc').forEach(el=>{
    const on = el.dataset.type === t;
    el.classList.toggle('active', on);
    el.setAttribute('aria-checked', on ? 'true' : 'false');
  });
  onType();
}

function openAdd(){
  RECONNECT_ID = null;
  document.getElementById('overlay').classList.add('show');
  document.getElementById('modal_title').textContent = 'Adicionar conta';
  document.getElementById('svc_grid').classList.remove('locked');
  document.getElementById('modal_msg').textContent='';
  document.getElementById('f_label').value='';
  document.getElementById('f_key').value='';
  cancelCurrentLogin();
  selectType('claude');
}

// Reconectar: reabre o popup travado no serviço da conta e atualiza as credenciais dela
function reconnect(id){
  const a = (LAST||[]).find(x=>x.id===id);
  if(!a) return;
  RECONNECT_ID = id;
  document.getElementById('overlay').classList.add('show');
  document.getElementById('modal_title').textContent = 'Reconectar conta';
  document.getElementById('modal_msg').textContent='';
  document.getElementById('f_key').value='';
  document.getElementById('f_label').value = a.label || '';
  cancelCurrentLogin();
  selectType(a.type);
  document.getElementById('svc_grid').classList.add('locked');
}

function closeAdd(){
  RECONNECT_ID = null;
  document.getElementById('overlay').classList.remove('show');
  document.getElementById('svc_grid').classList.remove('locked');
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
    cliBox.innerHTML = links + '.';
    actions.innerHTML = `<button class="primary" onclick="submitAdd()">Salvar</button>`;
  } else if(t==='claude'){
    keyBox.style.display='none';
    cliBox.innerHTML = `Entre na sua conta <b>Claude</b> pelo navegador.`;
    actions.innerHTML = `<div class="choice" style="width:100%">
      <button class="primary" onclick="startLogin('browser')">${svg('monitor')} Login</button>
    </div>`;
  } else {
    // codex: login por device-auth (QR/codigo). No PC mostra o QR; no celular, so o codigo/link.
    keyBox.style.display='none';
    cliBox.innerHTML = `Entre na sua conta <b>Codex</b>.`;
    actions.innerHTML = `<div class="choice" style="width:100%">
      <button class="primary" onclick="startLogin('phone')">${svg('monitor')} Login</button>
    </div>`;
  }
}

async function submitAdd(){
  const type = document.getElementById('f_type').value;
  const label = document.getElementById('f_label').value;
  const api_key = document.getElementById('f_key').value;
  setMsg('Salvando...');
  try{
    const r = await fetch('/api/accounts/add',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type,label,api_key,target_id:RECONNECT_ID||undefined})});
    const d = await r.json();
    if(d.ok){ setMsg('Conta adicionada!','ok'); setTimeout(()=>{closeAdd();load();},600); }
    else { setMsg(d.error||'Erro.','bad'); }
  }catch(e){ setMsg('Erro de conexao.','bad'); }
}

function resetLoginUI(){
  document.getElementById('login_step').innerHTML='';
  document.getElementById('qrbox').innerHTML='';
  document.getElementById('devcode_box').style.display='none';
  document.getElementById('copy_devcode').textContent='Copiar código';
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

async function copyText(text){
  if(!text) return false;
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    return true;
  }catch(e){ return false; }
}

async function copyDevCode(){
  const code = document.getElementById('devcode').textContent.trim();
  if(!code) return;
  const btn = document.getElementById('copy_devcode');
  const ok = await copyText(code);
  btn.textContent = ok ? 'Copiado!' : 'Selecione e copie';
  setTimeout(()=>{ btn.textContent='Copiar código'; }, ok ? 1800 : 2200);
}

// "abrir login": copia o codigo do device automaticamente e deixa o link abrir a pagina
function onOpenLogin(){
  const code = (document.getElementById('devcode').textContent || '').trim();
  if(code){
    copyText(code);
    const btn = document.getElementById('copy_devcode');
    if(btn){ btn.textContent = 'Copiado!'; setTimeout(()=>{ btn.textContent='Copiar código'; }, 1800); }
  }
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
  document.getElementById('modal_actions').innerHTML = '';
  try{
    const r = await fetch('/api/login/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({type,label,method,replace:true,target_id:RECONNECT_ID||undefined})});
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
  const mobile = isMobileDevice();

  // quando a URL fica disponivel, monta a tela (uma vez)
  if(d.url && !CURLOGIN.shown){
    CURLOGIN.shown=true;
    if(d.mode==='device'){
      // Codex: QR so no PC (no celular ja estamos no aparelho). O link "abrir login"
      // copia o codigo automaticamente e abre a pagina de login.
      const qb = document.getElementById('qrbox');
      if(mobile){ qb.style.display='none'; } else { qb.style.display='flex'; renderQR(d.url); }
      linkDiv.innerHTML = `<a href="${d.url}" target="_blank" rel="noopener" onclick="onOpenLogin()">${svg('link',13)} abrir login</a>`;
    } else {
      // Claude (paste): direcionamos a aba que abrimos no clique (uma janela so).
      if(CURLOGIN.win){ try{ CURLOGIN.win.location = d.url; }catch(e){ window.open(d.url,'_blank'); } }
      else { window.open(d.url,'_blank'); }
      linkDiv.innerHTML = `Se o navegador não abriu, clique aqui:<br>
        <a href="${d.url}" target="_blank" rel="noopener">${svg('link',13)} Abrir página de login</a>`;
    }
  }

  // codigo de device (Codex)
  if(d.mode==='device' && d.code){
    document.getElementById('devcode_box').style.display='block';
    document.getElementById('devcode').textContent = d.code;
  }

  if(d.status==='done'){
    resetLoginUI(); setMsg('Login concluído! ✓','ok'); CURLOGIN=null;
    setTimeout(()=>{closeAdd();load();}, 800); return;
  }
  if(d.status==='error'){ showLoginRetry(d.message||'Falha no login.'); return; }

  // textos de instrucao + campo de colar
  if(d.mode==='paste'){
    step.innerHTML = '1) Faça login na janela que abriu. &nbsp; 2) O site vai mostrar um <b>código</b>. &nbsp; 3) Cole o código aqui embaixo.';
    document.getElementById('code_box').style.display='block';
  } else if(d.mode==='device'){
    step.innerHTML = mobile
      ? '1) Toque em <b>abrir login</b> (o código já é copiado). &nbsp; 2) Cole/confirme o código na página. &nbsp; 3) Aguarde — conclui sozinho.'
      : '1) Escaneie o QR ou clique em <b>abrir login</b>. &nbsp; 2) Confirme o código mostrado acima. &nbsp; 3) Aguarde — conclui sozinho.';
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

async function renameAcc(id){
  const acc = LAST.find(a=>a.id===id);
  const atual = acc ? (acc.label || acc.typeLabel || '') : '';
  const label = prompt('Novo nome da conta:', atual);
  if(label === null) return;
  try{
    const r = await fetch('/api/accounts/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,label})});
    const d = await r.json();
    if(!d.ok){ alert(d.error||'Nao consegui renomear.'); return; }
    if(acc) acc.label = d.label;
    renderCards();
    load();
  }catch(e){ alert('Erro de conexao.'); }
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
document.getElementById('modal_close').innerHTML = svg('x');

// sincroniza o icone do seletor de tema com o tema atual
applyTheme(document.documentElement.getAttribute('data-theme'));

// fecha o modal clicando fora
document.getElementById('overlay').addEventListener('click', e=>{ if(e.target.id==='overlay') closeAdd(); });

// bottom sheet: arrastar para baixo fecha (so no celular, quando ja esta no topo)
(function(){
  const modal = document.querySelector('.modal');
  if(!modal) return;
  const isSheet = ()=> window.matchMedia('(max-width:600px)').matches;
  let startY=0, dy=0, dragging=false;
  modal.addEventListener('touchstart', e=>{
    if(!isSheet() || modal.scrollTop>0 || e.touches.length!==1){ dragging=false; return; }
    startY = e.touches[0].clientY; dy=0; dragging=true;
  }, {passive:true});
  modal.addEventListener('touchmove', e=>{
    if(!dragging) return;
    dy = e.touches[0].clientY - startY;
    if(dy>0){ modal.style.transition='none'; modal.style.transform='translateY('+dy+'px)'; }
  }, {passive:true});
  modal.addEventListener('touchend', ()=>{
    if(!dragging) return; dragging=false;
    modal.style.transition='transform .22s var(--ease)';
    if(dy>120){
      modal.style.transform='translateY(100%)';
      setTimeout(()=>{ modal.style.transform=''; modal.style.transition=''; closeAdd(); }, 200);
    } else {
      modal.style.transform='';
      setTimeout(()=>{ modal.style.transition=''; }, 220);
    }
  });
})();

// fecha o modal/folha com a tecla Esc
document.addEventListener('keydown', e=>{ if(e.key==='Escape' && document.getElementById('overlay').classList.contains('show')) closeAdd(); });

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
