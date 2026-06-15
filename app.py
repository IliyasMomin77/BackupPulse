import psycopg2
import requests
import os
import re
import json
import smtplib
import logging
import time
from logging.handlers import RotatingFileHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, jsonify, render_template

# ── Logging setup ──────────────────────────────────────────────
# logs/app.log    — all system activity (INFO+): startup, LLM calls, DB queries, route hits
# logs/errors.log — problems only (WARNING+): failed API calls, DB errors, exceptions
# logs/qa.jsonl   — user questions + bot answers (written by _log_qa, not the logger)
_log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(_log_dir, exist_ok=True)

_fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

_app_handler = RotatingFileHandler(
    os.path.join(_log_dir, 'app.log'), maxBytes=1_000_000, backupCount=3, encoding='utf-8')
_app_handler.setLevel(logging.INFO)
_app_handler.setFormatter(_fmt)

_err_handler = RotatingFileHandler(
    os.path.join(_log_dir, 'errors.log'), maxBytes=1_000_000, backupCount=3, encoding='utf-8')
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(_fmt)

logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(_app_handler)
logging.getLogger().addHandler(_err_handler)
log = logging.getLogger(__name__)


def _load_config():
    base = os.path.dirname(__file__)
    key_path = os.path.join(base, 'secret.key')
    cfg_path = os.path.join(base, 'config_final.env')

    fernet = None
    if os.path.exists(key_path):
        from cryptography.fernet import Fernet
        fernet = Fernet(open(key_path, 'rb').read())
        log.info("[CONFIG] Encryption key loaded from secret.key")
    else:
        log.warning("[CONFIG] secret.key not found — encrypted values will not be decrypted")

    with open(cfg_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if fernet and v.startswith('ENC:'):
                v = fernet.decrypt(v[4:].encode()).decode()
            os.environ.setdefault(k, v)
            os.environ[k] = v  # override so config always wins

_load_config()

app = Flask(__name__)
log.info(f"[STARTUP] provider={os.getenv('MODEL_PROVIDER','?')} port={os.getenv('FLASK_PORT','5000')} db={os.getenv('DB_NAME','?')}")

# Remarks store — persisted to remarks.json so restarts don't lose them
_REMARKS_FILE = os.path.join(os.path.dirname(__file__), 'remarks.json')

def _save_remarks(remarks):
    try:
        with open(_REMARKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(remarks, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_remarks = []  # always start fresh; saved to file mid-session only

# Incident resolution log — description + resolution only, used as LLM context
_INCIDENTS_LOG = os.path.join(_log_dir, 'incidents.jsonl')

def _log_incident_resolution(description: str, resolution: str):
    try:
        with open(_INCIDENTS_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "description": description,
                "resolution":  resolution,
                "resolved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False) + '\n')
        log.info(f"[INCIDENTS] resolution logged")
    except Exception as exc:
        log.warning(f"[INCIDENTS] write failed: {exc}")

def _find_past_resolution(error_text: str) -> str | None:
    """Scan incidents.jsonl for a past resolution matching the error. Returns suggestion string or None."""
    if not error_text or not os.path.exists(_INCIDENTS_LOG):
        return None
    keywords = [w for w in error_text.lower().split() if len(w) > 4]
    best, best_score = None, 0
    try:
        with open(_INCIDENTS_LOG, encoding='utf-8') as f:
            for line in f:
                try:
                    rec = json.loads(line.strip())
                except Exception:
                    continue
                rec_desc = (rec.get('description') or '').lower()
                score = sum(1 for kw in keywords if kw in rec_desc)
                if score > best_score:
                    best, best_score = rec, score
    except Exception:
        pass
    if best and best_score >= 3:
        return (f"[PAST RESOLUTION from incident log]\n"
                f"Similar error seen before: {best['description']}\n"
                f"How it was resolved: {best['resolution']}\n"
                f"Resolved on: {best.get('resolved_at','')}\n"
                f"Suggest this resolution if applicable.\n")
    return None

# ============================================================
# CONFIG
# ============================================================

MODEL_PROVIDER  = os.getenv("MODEL_PROVIDER", "groq")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL      = "https://generativelanguage.googleapis.com/v1beta/models/"

GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# LM Studio — local, air-gapped, zero external calls
LMSTUDIO_URL   = os.getenv("LMSTUDIO_URL",   "http://localhost:1234/v1/chat/completions")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "phi-3.5-mini-instruct")

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL      = "https://api.openai.com/v1/chat/completions"

COPILOT_API_KEY = os.getenv("COPILOT_API_KEY", "")
COPILOT_MODEL   = os.getenv("COPILOT_MODEL", "gpt-4o")
COPILOT_URL     = os.getenv("COPILOT_URL", "https://api.githubcopilot.com/chat/completions")

SNOW_INSTANCE         = os.getenv("SNOW_INSTANCE", "")
SNOW_USER             = os.getenv("SNOW_USER", "")
SNOW_PASS             = os.getenv("SNOW_PASS", "")
SNOW_ASSIGNMENT_GROUP = os.getenv("SNOW_ASSIGNMENT_GROUP", "")

SNOW_INSTANCE        = os.getenv("SNOW_INSTANCE", "")
SNOW_USER            = os.getenv("SNOW_USER", "")
SNOW_PASS            = os.getenv("SNOW_PASS", "")
SNOW_ASSIGNMENT_GROUP = os.getenv("SNOW_ASSIGNMENT_GROUP", "")

OLLAMA_URL      = os.getenv("OLLAMA_URL", "http://localhost:11434/v1/chat/completions")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "phi3.5")

# Known selectable models per provider (shown in the UI dropdown)
# Groq & OpenRouter: verified free models as of 2026
# LM Studio / Ollama: suggestions — model must be downloaded/loaded locally first
PROVIDER_MODELS = {
    # Groq — verified active June 2026 (decommissioned removed based on errors.log)
    "groq": [
        "llama-3.1-8b-instant",      # fastest, confirmed working
        "llama-3.3-70b-versatile",   # smarter, confirmed working
    ],
    # OpenRouter — verified free June 2026 from openrouter.ai/collections/free-models
    "openrouter": [
        "openai/gpt-oss-120b:free",   # OpenAI open-source 120B — best quality
        "openai/gpt-oss-20b:free",    # OpenAI open-source 20B — faster
        "google/gemma-4-31b-it:free", # Google Gemma 4 instruction tuned
    ],
    "gemini": [
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-3.5-turbo",
    ],
    "copilot": [
        "gpt-4o",
        "gpt-4",
        "gpt-3.5-turbo",
    ],
    # Local models — must be downloaded and loaded in LM Studio / pulled in Ollama first
    # Q4_K_M quantization recommended (~2-3 GB RAM each).
    "lmstudio": [
        "phi-3.5-mini-instruct",   # 3.8B — best instruction following on CPU
        "phi-4-mini-instruct",     # 3.8B — newer Phi, better reasoning
    ],
    "ollama": [
        "phi3:mini",
    ],
}


def _get_active_model():
    return {
        "groq":        GROQ_MODEL,
        "openrouter":  OPENROUTER_MODEL,
        "gemini":      GEMINI_MODEL,
        "openai":      OPENAI_MODEL,
        "copilot":     COPILOT_MODEL,
        "lmstudio":    LMSTUDIO_MODEL,
        "ollama":      OLLAMA_MODEL,
    }.get(MODEL_PROVIDER, "")


DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "veeam_demo"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "")
}

FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

SMTP_HOST  = os.getenv("SMTP_HOST", "")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER", "")
SMTP_PASS  = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "veeam-reports@yourdomain.com")
EMAIL_TO   = os.getenv("EMAIL_TO", "")

# ============================================================
# SYSTEM PROMPTS
# ============================================================

def _load_file(filename, fallback=""):
    path = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return fallback

_LOCAL_PROVIDERS = {"lmstudio", "ollama"}
if MODEL_PROVIDER in _LOCAL_PROVIDERS:
    _SYSTEM_PROMPT = _load_file("system_prompt_local.txt", "You are a Veeam backup assistant. Explain the results clearly.")
    _SQL_PROMPT    = _load_file("sql_prompt_local.txt",    "Return a PostgreSQL SELECT query only. No explanation.")
else:
    _SYSTEM_PROMPT = _load_file("system_prompt.txt", "You are VeeamBot, a Veeam Backup expert. Speak naturally.")
    _SQL_PROMPT    = _load_file("sql_prompt.txt",    "Generate a PostgreSQL SELECT query. Return SQL only.")

def load_system_prompt(): return _SYSTEM_PROMPT
def load_sql_prompt():    return _SQL_PROMPT

# ============================================================
# LLM
# ============================================================

def call_openai_compatible(prompt, system, url, api_key, model, max_tokens=500):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    req_timeout = 300 if "localhost" in url or "127.0.0.1" in url else 30
    log.info(f"[LLM] calling model={model} max_tokens={max_tokens} prompt_len={len(prompt)}")
    t0 = time.perf_counter()
    r = requests.post(
        url,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        json={"model": model, "messages": msgs, "temperature": 0.1, "max_tokens": max_tokens},
        timeout=req_timeout
    )
    duration = round(time.perf_counter() - t0, 2)
    if not r.ok:
        log.error(f"[LLM] FAILED model={model} status={r.status_code} duration={duration}s error={r.text[:200]}")
        raise Exception(f"API error {r.status_code}: {r.text}")
    data = r.json()
    if "choices" not in data:
        log.error(f"[LLM] unexpected response model={model}: {str(data)[:200]}")
        raise Exception(f"Unexpected API response: {data}")
    reply = data["choices"][0]["message"]["content"].strip()
    log.info(f"[LLM] OK model={model} duration={duration}s reply_len={len(reply)}")
    return reply


def call_gemini(prompt, system=None):
    full = (system + "\n\n" + prompt) if system else prompt
    url = GEMINI_URL + GEMINI_MODEL + ":generateContent?key=" + GEMINI_API_KEY
    log.info(f"[LLM] calling model={GEMINI_MODEL} prompt_len={len(full)}")
    t0 = time.perf_counter()
    r = requests.post(url, headers={"Content-Type": "application/json"},
                      json={"contents": [{"parts": [{"text": full}]}]}, timeout=60)
    duration = round(time.perf_counter() - t0, 2)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        log.error(f"[LLM] Gemini FAILED duration={duration}s error={data['error']}")
        raise Exception("Gemini error: " + data["error"].get("message", str(data["error"])))
    reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    log.info(f"[LLM] OK model={GEMINI_MODEL} duration={duration}s reply_len={len(reply)}")
    return reply


def call_llm(prompt, system=None, max_tokens=500):
    if MODEL_PROVIDER == "gemini":
        return call_gemini(prompt, system)
    elif MODEL_PROVIDER == "groq":
        return call_openai_compatible(prompt, system, GROQ_URL, GROQ_API_KEY, GROQ_MODEL, max_tokens)
    elif MODEL_PROVIDER == "openai":
        return call_openai_compatible(prompt, system, OPENAI_URL, OPENAI_API_KEY, OPENAI_MODEL, max_tokens)
    elif MODEL_PROVIDER == "copilot":
        return call_openai_compatible(prompt, system, COPILOT_URL, COPILOT_API_KEY, COPILOT_MODEL, max_tokens)
    elif MODEL_PROVIDER == "openrouter":
        return call_openai_compatible(prompt, system, OPENROUTER_URL, OPENROUTER_API_KEY, OPENROUTER_MODEL, max_tokens)
    elif MODEL_PROVIDER == "lmstudio":
        return call_openai_compatible(prompt, system, LMSTUDIO_URL, "lm-studio", LMSTUDIO_MODEL, max_tokens)
    elif MODEL_PROVIDER == "ollama":
        return call_openai_compatible(prompt, system, OLLAMA_URL, "", OLLAMA_MODEL, max_tokens)
    return "No valid provider configured."


# ============================================================
# DATABASE
# ============================================================

def run_query(sql):
    t0 = time.perf_counter()
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        duration = round(time.perf_counter() - t0, 3)
        log.info(f"[DB] rows={len(rows)} duration={duration}s sql={sql[:120].replace(chr(10),' ')}")
        return rows
    except Exception as e:
        log.error(f"[DB] FAILED sql={sql[:120].replace(chr(10),' ')} error={e}")
        raise
    finally:
        conn.close()


# ============================================================
# CHAT LOGIC
# ============================================================

_INSTANT_REPLY = re.compile(
    r'^\s*(hi|hello|hey|thanks|thank you|ok|okay|cool|got it|'
    r'sounds good|perfect|great|nice|awesome|noted)\s*[!.,]?\s*$',
    re.IGNORECASE
)

def _format_rows(question, rows):
    """Format query results as plain text for local models (no second LLM call)."""
    if not rows:
        return "No records found."
    keys = list(rows[0].keys())

    # Failed jobs — group by vbr_server → job_name
    if "failed_object_name" in keys:
        from collections import defaultdict
        grouped = defaultdict(lambda: defaultdict(list))
        for r in rows[:60]:
            srv  = r.get("vbr_server", "?")
            job  = r.get("job_name", "?")
            obj  = r.get("failed_object_name", "?")
            otype = r.get("object_type", "")
            msg  = r.get("failure_message", "")
            label = f"{obj}" + (f" ({otype})" if otype else "")
            entry = (label, msg)
            if entry not in grouped[srv][job]:
                grouped[srv][job].append(entry)
        total = sum(len(o) for s in grouped.values() for o in s.values())
        lines = [f"**{total} failed backup(s):**\n"]
        for srv, jobs in grouped.items():
            lines.append(f"**{srv}**")
            for job, entries in jobs.items():
                lines.append(f"  • {job}")
                for label, msg in entries:
                    lines.append(f"    – {label} ❌")
                    if msg:
                        lines.append(f"      _{msg}_")
        lines.append("\nReview and retry failed jobs or check the affected servers.")
        return "\n".join(lines)

    # Long-running jobs
    if "duration_hours" in keys:
        lines = ["**Long-running jobs:**"]
        for r in rows:
            alert = r.get("alert_level", "")
            flag = " 🔴" if alert == "Critical" else " ⚠️"
            lines.append(f"  • **{r.get('job_name')}** on {r.get('vbr_server')} — "
                         f"{r.get('duration_hours')}h{flag}")
        return "\n".join(lines)

    # Job sessions
    if "status" in keys and "duration_minutes" in keys:
        from collections import Counter
        counts = Counter(r.get("status") for r in rows)
        lines = [f"**{len(rows)} job session(s):**",
                 f"  ✅ Success: {counts.get('Success',0)}  "
                 f"⚠️ Warning: {counts.get('Warning',0)}  "
                 f"❌ Failed: {counts.get('Failed',0)}"]
        for r in [x for x in rows if x.get("status") == "Failed"][:5]:
            lines.append(f"  • **{r.get('job_name')}** — {r.get('failure_message','')}")
        return "\n".join(lines)

    # Single object detail
    if "last_successful_backup" in keys and "active_restore_points" in keys and len(rows) == 1:
        r = rows[0]
        return "\n".join([
            f"**{r.get('object_name')}** ({r.get('object_type')}) ✅",
            f"  Job: {r.get('job_name')}  |  Server: {r.get('vbr_server')}",
            f"  Last backup: {r.get('last_successful_backup')}",
            f"  Restore points: {r.get('active_restore_points')}  |  Size: {r.get('size_gb')} GB",
        ])

    # Protected objects list (multiple rows)
    if "last_successful_backup" in keys and len(rows) > 1:
        lines = [f"**{len(rows)} protected object(s):**"]
        for r in rows[:40]:
            lines.append(f"  • **{r.get('object_name')}** ({r.get('object_type')}) — "
                         f"last backup: {r.get('last_successful_backup')}")
        return "\n".join(lines)

    # Restore points
    if "restore_point_date" in keys:
        obj = rows[0].get("object_name", "")
        lines = [f"**Restore points{' for ' + obj if obj else ''}:**"]
        for r in rows[:10]:
            lines.append(f"  • {r.get('restore_point_date')}  –  {r.get('backup_type')}  ({r.get('size_gb')} GB)")
        return "\n".join(lines)

    # Repositories
    if "used_pct" in keys:
        lines = ["**Repository capacity:**"]
        for r in rows:
            pct = float(r.get("used_pct", 0))
            flag = " ⚠️" if pct >= 90 else ""
            lines.append(f"  • **{r.get('repo_name')}** on {r.get('vbr_server')} — "
                         f"{pct}% used  ({r.get('free_tb')} TB free){flag}")
        return "\n".join(lines)

    # Single-column list
    if len(keys) == 1:
        items = [str(r[keys[0]]) for r in rows]
        return f"**{len(items)} result(s):**\n" + "\n".join(f"  • {i}" for i in items)

    # Fallback table
    lines = [" | ".join(str(k) for k in keys)]
    for r in rows[:30]:
        lines.append(" | ".join(str(r.get(k, "")) for k in keys))
    return "\n".join(lines)


def _log_qa(question, answer, duration, provider):
    entry = {
        "ts":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "answer":   answer,
        "provider": provider,
        "duration": duration,
    }
    try:
        qa_file = os.path.join(_log_dir, 'qa.jsonl')
        with open(qa_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as exc:
        log.warning(f"[QA LOG] write failed: {exc}")


_SNOW_TRIGGER = re.compile(
    r'\b(create|raise|open|log|file)\s+(im|incident|ticket|inc)\b',
    re.IGNORECASE
)
_SNOW_RESOLVE_TRIGGER = re.compile(
    r'\b(resolve|close|fix|solved?|complete)\s+(im|incident|ticket|inc)\b',
    re.IGNORECASE
)


def create_snow_incident(short_description, description, urgency=2, impact=2):
    from requests.auth import HTTPBasicAuth
    if not SNOW_INSTANCE or not SNOW_USER:
        raise Exception("ServiceNow not configured. Add SNOW_INSTANCE, SNOW_USER, SNOW_PASS to config_final.env")
    url = f"{SNOW_INSTANCE}/api/now/table/incident"
    payload = {
        "short_description": short_description,
        "description":       description,
        "urgency":           str(urgency),
        "impact":            str(impact),
        "category":          "infrastructure",
        "subcategory":       "backup",
        "caller_id":         SNOW_USER,
    }
    if SNOW_ASSIGNMENT_GROUP:
        payload["assignment_group"] = SNOW_ASSIGNMENT_GROUP
    log.info(f"[SNOW] creating incident short_desc={short_description!r} urgency={urgency}")
    r = requests.post(
        url,
        auth=HTTPBasicAuth(SNOW_USER, SNOW_PASS),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json=payload,
        timeout=30
    )
    if not r.ok:
        log.error(f"[SNOW] FAILED status={r.status_code} error={r.text[:200]}")
        raise Exception(f"ServiceNow error {r.status_code}: {r.text[:200]}")
    result = r.json()["result"]
    inc_number = result["number"]
    inc_url    = f"{SNOW_INSTANCE}/nav_to.do?uri=incident.do?sysparm_query=number={inc_number}"
    log.info(f"[SNOW] created {inc_number}")
    return inc_number, inc_url


def resolve_snow_incident(inc_number, resolution_code, resolution_notes):
    from requests.auth import HTTPBasicAuth
    if not SNOW_INSTANCE or not SNOW_USER:
        raise Exception("ServiceNow not configured.")
    auth = HTTPBasicAuth(SNOW_USER, SNOW_PASS)
    # Get sys_id by incident number
    r = requests.get(
        f"{SNOW_INSTANCE}/api/now/table/incident",
        auth=auth,
        headers={"Accept": "application/json"},
        params={"sysparm_query": f"number={inc_number}", "sysparm_fields": "sys_id,number,short_description,description"},
        timeout=30
    )
    results = r.json().get("result", [])
    if not results:
        raise Exception(f"Incident {inc_number} not found in ServiceNow")
    sys_id      = results[0]["sys_id"]
    description = results[0].get("description") or results[0].get("short_description", "")
    log.info(f"[SNOW] resolving {inc_number} sys_id={sys_id} code={resolution_code!r}")
    r = requests.patch(
        f"{SNOW_INSTANCE}/api/now/table/incident/{sys_id}",
        auth=auth,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json={
            "state":      "6",
            "close_code":  resolution_code,
            "close_notes": resolution_notes,
            "resolved_by": SNOW_USER,
        },
        timeout=30
    )
    if not r.ok:
        log.error(f"[SNOW] resolve FAILED status={r.status_code} error={r.text[:200]}")
        raise Exception(f"ServiceNow error {r.status_code}: {r.text[:200]}")
    log.info(f"[SNOW] resolved {inc_number}")
    return inc_number, description


def process_question(question):
    t0 = time.perf_counter()
    log.info(f"[CHAT] question={question!r}")
    reply = ""

    # Greetings — instant, no LLM call
    if _INSTANT_REPLY.match(question):
        reply = "Hey! Ask me anything about your backup environment — failed jobs, restore points, repository capacity, or generate a health report."
        log.info(f"[CHAT] instant greeting — {round(time.perf_counter()-t0,3)}s")

    elif _SNOW_RESOLVE_TRIGGER.search(question):
        m = re.search(r'\bINC\d+\b', question, re.IGNORECASE)
        inc_number = m.group().upper() if m else None
        if not inc_number:
            reply = "Please include the incident number. Example: **Resolve INC0010001**"
        else:
            return {"answer": f"To resolve **{inc_number}**, fill in the mandatory fields below:", "snow_form": {"type": "resolve", "inc_number": inc_number}}

    elif _SNOW_TRIGGER.search(question):
        try:
            extract_prompt = (
                f'The engineer wants to create a ServiceNow incident. Their message:\n"{question}"\n\n'
                'Extract the following fields:\n'
                '- short_description: one-line summary, max 100 chars\n'
                '- description: full details of the issue\n'
                '- urgency: integer 1=Critical 2=High 3=Medium (default 2)\n\n'
                'Return JSON only, no explanation:\n'
                '{"short_description": "...", "description": "...", "urgency": 2}'
            )
            raw = call_llm(extract_prompt, "Extract incident fields. Return JSON only.", max_tokens=200)
            m = re.search(r'\{.*?\}', raw, re.DOTALL)
            inc_data = json.loads(m.group()) if m else {}
            short_desc = inc_data.get("short_description") or question[:100]
            description = inc_data.get("description") or question
            urgency     = int(inc_data.get("urgency", 2))
            urgency_label = {1: "Critical", 2: "High", 3: "Medium"}.get(urgency, "High")

            inc_number, inc_url = create_snow_incident(short_desc, description, urgency=urgency)
            reply = (
                f"✅ Incident **{inc_number}** raised in ServiceNow\n\n"
                f"**Summary:** {short_desc}\n"
                f"**Priority:** {urgency_label}\n"
                f"**View:** {inc_url}"
            )
            log.info(f"[CHAT] SNOW incident created {inc_number} — {round(time.perf_counter()-t0,3)}s")
        except Exception as e:
            log.error(f"[CHAT] SNOW create failed: {e}")
            reply = f"Failed to create ServiceNow incident: {e}"

    else:
        # Build SQL via LLM
        if MODEL_PROVIDER in _LOCAL_PROVIDERS:
            raw_sql = call_llm(question, load_sql_prompt(), max_tokens=250)
        else:
            sql_user = (
                "Generate a PostgreSQL SELECT query to answer this question:\n"
                + question + "\n"
                "If NOT a data question, reply DIRECT: followed by a short answer.\n"
                "Otherwise SQL only. No markdown."
            )
            raw_sql = call_llm(sql_user, load_sql_prompt(), max_tokens=300)
            sql_clean = re.sub(r"```sql|```", "", raw_sql).strip()
            if sql_clean.upper().startswith("DIRECT:"):
                reply = sql_clean[7:].strip()
                log.info(f"[CHAT] DIRECT answer — {round(time.perf_counter()-t0,3)}s")

        if not reply and not raw_sql:
            reply = "The AI did not return a response. Please try again."
            log.warning("[CHAT] LLM returned None/empty for SQL generation")

        if not reply:
            # Strip markdown fences and extract the SELECT statement
            sql = re.sub(r"```sql|```", "", raw_sql).strip()
            if not sql.upper().startswith("SELECT"):
                m = re.search(r"(SELECT\b.+)", sql, re.IGNORECASE | re.DOTALL)
                sql = m.group(1).strip() if m else sql

            forbidden = ["update", "delete", "insert", "drop", "alter", "truncate"]
            if any(w in sql.lower() for w in forbidden):
                log.warning(f"[CHAT] blocked write SQL attempt — {sql[:80]}")
                reply = "I can only read backup data. Write operations are not allowed."

            else:
                rows = run_query(sql)
                if not rows:
                    log.info(f"[CHAT] query returned 0 rows — {round(time.perf_counter()-t0,3)}s")
                    reply = "I checked the database and there's nothing to report here — either everything looks clean or this data isn't available. You might want to check with your Backup Administrator."

                elif MODEL_PROVIDER in _LOCAL_PROVIDERS:
                    reply = _format_rows(question, rows)
                    log.info(f"[CHAT] local format done rows={len(rows)} — {round(time.perf_counter()-t0,3)}s")

                else:
                    # Check incident resolution log for past fixes on same error
                    past_fix = ""
                    for row in rows[:5]:
                        err = row.get("failure_message") or row.get("failure_message", "")
                        if err:
                            match = _find_past_resolution(err)
                            if match:
                                past_fix = match
                                break

                    explain_prompt = (
                        "[QUERY RESULTS]\n" + sql + "\n" + str(rows[:50])
                        + "\n\n[USER QUESTION]\n" + question
                        + ("\n\n" + past_fix if past_fix else "")
                        + "\n\nReply in a helpful, conversational tone — like a knowledgeable colleague, "
                        "not a formal report. Lead with the key finding, use bullet points for lists, "
                        "bold the most important names and numbers with **text**, and suggest a clear "
                        "next step if there is a problem."
                    )
                    reply = call_llm(explain_prompt, load_system_prompt(), max_tokens=450)
                    log.info(f"[CHAT] cloud explain done rows={len(rows)} — {round(time.perf_counter()-t0,3)}s")

    _log_qa(question, reply, round(time.perf_counter() - t0, 3), MODEL_PROVIDER)
    return reply


# ============================================================
# HEALTH CHECK EMAIL
# ============================================================

@app.template_filter('dt')
def _filter_dt(value):
    return str(value)[:16] if value else ''


def generate_health_email(failed_24h, critical_repos, incomplete_jobs, remarks=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_issues = bool(failed_24h or critical_repos or incomplete_jobs)
    return render_template(
        'health_email.html',
        ts=ts,
        overall="CRITICAL ISSUES FOUND" if has_issues else "ALL SYSTEMS HEALTHY",
        overall_bg="#DC2626" if has_issues else "#16A34A",
        f_cls="red"    if failed_24h    else "green",
        r_cls="red"    if critical_repos else "green",
        j_cls="yellow" if incomplete_jobs else "green",
        failed_24h=failed_24h,
        critical_repos=critical_repos,
        incomplete_jobs=incomplete_jobs,
        remarks=remarks or [],
    )


def _fetch_health_data():
    failed_24h = run_query("""
        SELECT MAX(backup_date) AS backup_date, vbr_server, job_name, failed_object_name
        FROM failed_jobs_daily
        WHERE backup_date >= CURRENT_DATE - INTERVAL '1 day'
        GROUP BY vbr_server, job_name, failed_object_name
        ORDER BY vbr_server, job_name, failed_object_name
    """)
    critical_repos = run_query("""
        SELECT repo_name, sobr_name, repo_type, vbr_server,
               total_tb, used_tb, free_tb, used_pct
        FROM repositories
        WHERE used_pct >= 90
        ORDER BY used_pct DESC
    """)
    incomplete_jobs = run_query("""
        SELECT job_name, backup_type, vbr_server,
               start_time, end_time, duration_minutes, status, failure_message
        FROM job_sessions
        WHERE start_time >= NOW() - INTERVAL '24 hours'
          AND status IN ('Failed', 'Warning')
        ORDER BY status DESC, start_time DESC
    """)
    return failed_24h, critical_repos, incomplete_jobs


# ============================================================
# ROUTES
# ============================================================

def _configured_providers():
    """Return providers to show in the UI — controlled by ENABLED_PROVIDERS in config."""
    enabled = {p.strip() for p in os.getenv("ENABLED_PROVIDERS", "groq,openrouter,lmstudio").split(",")}
    order   = ["groq", "openrouter", "lmstudio", "gemini", "openai", "copilot", "ollama"]
    return [p for p in order if p in enabled]


@app.route("/")
def index():
    return render_template('chat.html',
        provider=MODEL_PROVIDER,
        active_model=_get_active_model(),
        email_to=EMAIL_TO,
        providers=_configured_providers(),
        provider_models=PROVIDER_MODELS,
    )


@app.route("/switch-provider", methods=["POST"])
def switch_provider():
    global MODEL_PROVIDER, _SYSTEM_PROMPT, _SQL_PROMPT
    global GROQ_MODEL, OPENROUTER_MODEL, GEMINI_MODEL, OPENAI_MODEL, COPILOT_MODEL, LMSTUDIO_MODEL, OLLAMA_MODEL
    data = request.get_json()
    provider = data.get("provider", "").strip().lower()
    model    = data.get("model",    "").strip()
    valid = {"groq", "openrouter", "gemini", "openai", "copilot", "lmstudio", "ollama"}
    if provider not in valid:
        return jsonify({"error": f"Unknown provider: {provider}"}), 400

    old_provider = MODEL_PROVIDER
    old_model    = _get_active_model()
    MODEL_PROVIDER = provider

    # Update the model for this provider if one was sent
    if model:
        if   provider == "groq":       GROQ_MODEL       = model
        elif provider == "openrouter": OPENROUTER_MODEL = model
        elif provider == "gemini":     GEMINI_MODEL     = model
        elif provider == "openai":     OPENAI_MODEL     = model
        elif provider == "copilot":    COPILOT_MODEL    = model
        elif provider == "lmstudio":   LMSTUDIO_MODEL   = model
        elif provider == "ollama":     OLLAMA_MODEL     = model

    if provider in _LOCAL_PROVIDERS:
        _SYSTEM_PROMPT = _load_file("system_prompt_local.txt", "You are a Veeam backup assistant. Explain the results clearly.")
        _SQL_PROMPT    = _load_file("sql_prompt_local.txt",    "Return a PostgreSQL SELECT query only. No explanation.")
    else:
        _SYSTEM_PROMPT = _load_file("system_prompt.txt", "You are VeeamBot, a Veeam Backup expert. Speak naturally.")
        _SQL_PROMPT    = _load_file("sql_prompt.txt",    "Generate a PostgreSQL SELECT query. Return SQL only.")

    active = _get_active_model()
    log.info(f"[SWITCH] {old_provider}/{old_model} -> {provider}/{active}")
    return jsonify({"success": True, "provider": provider, "model": active})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"answer": "Please ask a question."})
    try:
        result = process_question(question)
        if isinstance(result, dict):
            return jsonify(result)
        return jsonify({"answer": result})
    except Exception as e:
        log.error(f"[ROUTE /chat] error={e}")
        return jsonify({"answer": "Error: " + str(e)})


@app.route("/create-incident", methods=["POST"])
def create_incident_route():
    data        = request.get_json()
    short_desc  = data.get("short_description", data.get("description", "")).strip()[:100]
    description = data.get("description", short_desc).strip()
    urgency     = int(data.get("urgency", 2))
    if not short_desc:
        return jsonify({"error": "Short description required"}), 400
    urgency_label = {1: "Critical", 2: "High", 3: "Medium"}.get(urgency, "High")
    log.info(f"[ROUTE /create-incident] short_desc={short_desc!r} urgency={urgency}")
    try:
        inc_number, inc_url = create_snow_incident(short_desc, description, urgency=urgency)
        return jsonify({"success": True, "message": f"✅ Incident **{inc_number}** created in ServiceNow.\n\n**Summary:** {short_desc}\n**Priority:** {urgency_label}\n**View:** {inc_url}"})
    except Exception as e:
        log.error(f"[ROUTE /create-incident] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/list-incidents")
def list_incidents_route():
    from requests.auth import HTTPBasicAuth
    if not SNOW_INSTANCE or not SNOW_USER:
        return jsonify({"error": "ServiceNow not configured"}), 400
    try:
        r = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/incident",
            auth=HTTPBasicAuth(SNOW_USER, SNOW_PASS),
            headers={"Accept": "application/json"},
            params={
                "sysparm_fields":        "number,short_description,description,state,urgency,opened_at,assigned_to",
                "sysparm_query":         f"caller_id.user_name={SNOW_USER}^state!=6^state!=7",
                "sysparm_display_value": "true",
                "sysparm_limit":         20,
                "sysparm_orderby":       "opened_at^DESC",
            },
            timeout=30
        )
        if not r.ok:
            return jsonify({"error": f"SNOW error {r.status_code}"}), 500
        items = []
        for rec in r.json().get("result", []):
            items.append({
                "number":      rec.get("number",""),
                "summary":     rec.get("short_description",""),
                "description": rec.get("description",""),
                "state":       rec.get("state",""),
                "urgency":     rec.get("urgency",""),
                "assigned_to": rec.get("assigned_to") or "Unassigned",
                "opened_at":   str(rec.get("opened_at",""))[:16],
            })
        log.info(f"[ROUTE /list-incidents] returned {len(items)} incidents")
        return jsonify({"incidents": items})
    except Exception as e:
        log.error(f"[ROUTE /list-incidents] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/get-incident")
def get_incident_route():
    from requests.auth import HTTPBasicAuth
    if not SNOW_INSTANCE or not SNOW_USER:
        return jsonify({"error": "ServiceNow not configured"}), 400
    inc_number = request.args.get("number", "").strip().upper()
    if not inc_number:
        return jsonify({"error": "Incident number required"}), 400
    try:
        r = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/incident",
            auth=HTTPBasicAuth(SNOW_USER, SNOW_PASS),
            headers={"Accept": "application/json"},
            params={
                "sysparm_query":         f"number={inc_number}",
                "sysparm_fields":        "number,short_description,description,state,urgency,priority,assigned_to,assignment_group,opened_at,resolved_at,close_notes,caller_id",
                "sysparm_display_value": "true",
                "sysparm_limit":         1,
            },
            timeout=30
        )
        if not r.ok:
            return jsonify({"error": f"SNOW error {r.status_code}"}), 500
        results = r.json().get("result", [])
        if not results:
            return jsonify({"error": f"{inc_number} not found in ServiceNow"}), 404
        rec = results[0]
        log.info(f"[ROUTE /get-incident] fetched {inc_number}")
        return jsonify({"incident": {
            "number":           rec.get("number", ""),
            "summary":          rec.get("short_description", ""),
            "description":      rec.get("description", ""),
            "state":            rec.get("state", ""),
            "urgency":          rec.get("urgency", ""),
            "priority":         rec.get("priority", ""),
            "assigned_to":      rec.get("assigned_to") or "Unassigned",
            "assignment_group": rec.get("assignment_group") or "—",
            "opened_at":        str(rec.get("opened_at", ""))[:16],
            "resolved_at":      str(rec.get("resolved_at", ""))[:16],
            "close_notes":      rec.get("close_notes", ""),
        }})
    except Exception as e:
        log.error(f"[ROUTE /get-incident] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/resolve-incident", methods=["POST"])
def resolve_incident_route():
    data = request.get_json()
    inc_number      = data.get("inc_number", "").strip().upper()
    resolution_code  = data.get("resolution_code", "Solved (Permanently)").strip()
    resolution_notes = data.get("resolution_notes", "").strip()
    if not inc_number:
        return jsonify({"error": "Incident number required"}), 400
    if not resolution_notes:
        return jsonify({"error": "Resolution notes are required"}), 400
    log.info(f"[ROUTE /resolve-incident] {inc_number} code={resolution_code!r}")
    try:
        inc_number, description = resolve_snow_incident(inc_number, resolution_code, resolution_notes)
        _log_incident_resolution(description or inc_number, resolution_notes)
        return jsonify({"success": True, "message": f"✅ Incident **{inc_number}** resolved successfully in ServiceNow."})
    except Exception as e:
        log.error(f"[ROUTE /resolve-incident] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/healthcheck")
def healthcheck():
    log.info("[ROUTE /healthcheck] generating health report")
    t0 = time.perf_counter()
    try:
        failed_24h, critical_repos, incomplete_jobs = _fetch_health_data()
        log.info(f"[ROUTE /healthcheck] failed={len(failed_24h)} repos={len(critical_repos)} jobs={len(incomplete_jobs)} remarks={len(_remarks)} — {round(time.perf_counter()-t0,3)}s")
        return jsonify({
            "email_html": generate_health_email(failed_24h, critical_repos, incomplete_jobs, list(_remarks)),
            "counts": {
                "failed": len(failed_24h),
                "repos":  len(critical_repos),
                "jobs":   len(incomplete_jobs),
                "remarks": len(_remarks)
            }
        })
    except Exception as e:
        log.error(f"[ROUTE /healthcheck] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/add-remark", methods=["POST"])
def add_remark():
    data = request.get_json()
    instruction = data.get("instruction", "").strip()
    if not instruction:
        return jsonify({"error": "No instruction provided"}), 400
    log.info(f"[ROUTE /add-remark] instruction={instruction!r}")
    try:
        parse_prompt = (
            'You are a Veeam backup admin assistant parsing an engineer\'s action update.\n'
            f'The update may mention ONE or MULTIPLE jobs/actions. Extract ALL of them.\n'
            f'Input: "{instruction}"\n\n'
            'Return a JSON ARRAY — one object per job/action mentioned. Example:\n'
            '[\n'
            '  {"job_name": "Prod_VM_Job", "vbr_server": "VBR1 or Unknown", "vm_object": "VM name or Unknown",\n'
            '   "action": "Backup Retry", "status": "Running",\n'
            '   "note": "Backup retry initiated, job currently running"},\n'
            '  {"job_name": "Linux_VM_Job", "vbr_server": "VBR2 or Unknown", "vm_object": "Unknown",\n'
            '   "action": "Backup Retry", "status": "Completed",\n'
            '   "note": "Backup retry completed successfully"}\n'
            ']\n\n'
            'Status rules:\n'
            '- completed/success/fixed/resolved → Completed\n'
            '- running/retried/retry/running now → Running\n'
            '- case raised/ticket/INC/troubleshoot/investigating → Case Raised\n'
            '- will/scheduled/planned/monitor → Pending\n'
            'Return JSON array only. No explanation.'
        )
        raw = call_llm(parse_prompt, "Extract structured data. Return only a JSON array.", max_tokens=400)

        # Try array first, fallback to single object
        arr_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if arr_match:
            parsed = json.loads(arr_match.group())
            if isinstance(parsed, dict):
                parsed = [parsed]
        else:
            obj_match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
            if not obj_match:
                return jsonify({"error": "Could not parse remarks from instruction"}), 400
            parsed = [json.loads(obj_match.group())]

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_remarks = []
        for r in parsed:
            r["timestamp"] = ts
            _remarks.append(r)
            new_remarks.append(r)
        _save_remarks(_remarks)
        log.info(f"[ROUTE /add-remark] added {len(new_remarks)} remark(s), total={len(_remarks)}")

        failed_24h, critical_repos, incomplete_jobs = _fetch_health_data()
        email_html = generate_health_email(failed_24h, critical_repos, incomplete_jobs, list(_remarks))

        lines = []
        for r in new_remarks:
            job  = r.get('job_name', 'Unknown')
            vbr  = r.get('vbr_server', '')
            act  = r.get('action', '')
            stat = r.get('status', '')
            ctx  = job + (f" ({vbr})" if vbr and vbr != 'Unknown' else "")
            lines.append(f"- **{ctx}** — {act} [{stat}]")

        summary = "\n".join(lines)
        count = len(new_remarks)
        msg = f"Logged {count} remark{'s' if count > 1 else ''}:\n{summary}\n\nReport updated. Send when ready."
        return jsonify({
            "html": email_html,
            "remark": new_remarks[0],
            "message": msg
        })
    except Exception as e:
        log.error(f"[ROUTE /add-remark] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/remove-remark", methods=["POST"])
def remove_remark():
    data = request.get_json()
    instruction = data.get("instruction", "").strip()
    if not instruction:
        return jsonify({"error": "No instruction provided"}), 400
    if not _remarks:
        return jsonify({"error": "No remarks to remove."}), 400
    log.info(f"[ROUTE /remove-remark] instruction={instruction!r}")
    try:
        # Ask LLM to identify which remark to remove
        list_str = "\n".join(
            f"{i}: job={r.get('job_name','?')} vm={r.get('vm_object','?')} action={r.get('action','?')} status={r.get('status','?')} time={r.get('timestamp','?')}"
            for i, r in enumerate(_remarks)
        )
        parse_prompt = (
            f'The engineer wants to remove one remark. Their instruction: "{instruction}"\n\n'
            f'Current remarks (index: details):\n{list_str}\n\n'
            'Return ONLY the integer index of the remark to remove. Nothing else.'
        )
        raw = call_llm(parse_prompt, "Return only a single integer index.", max_tokens=10)
        idx_match = re.search(r'\d+', raw.strip())
        if not idx_match:
            return jsonify({"error": "Could not identify which remark to remove."}), 400
        idx = int(idx_match.group())
        if idx < 0 or idx >= len(_remarks):
            return jsonify({"error": f"Remark index {idx} out of range."}), 400

        removed = _remarks.pop(idx)
        _save_remarks(_remarks)
        log.info(f"[ROUTE /remove-remark] removed idx={idx} job={removed.get('job_name','?')} remaining={len(_remarks)}")
        failed_24h, critical_repos, incomplete_jobs = _fetch_health_data()
        email_html = generate_health_email(failed_24h, critical_repos, incomplete_jobs, list(_remarks))
        job = removed.get('job_name', 'Unknown')
        obj = removed.get('vm_object', '')
        ctx = job + (f" / {obj}" if obj and obj != 'Unknown' else "")
        return jsonify({
            "html": email_html,
            "message": f"Removed remark for **{ctx}**. {len(_remarks)} remark(s) remaining."
        })
    except Exception as e:
        log.error(f"[ROUTE /remove-remark] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/clear-remarks", methods=["POST"])
def clear_remarks():
    count = len(_remarks)
    _remarks.clear()
    _save_remarks(_remarks)
    log.info(f"[ROUTE /clear-remarks] cleared {count} remark(s)")
    return jsonify({"success": True, "message": "All remarks cleared."})


@app.route("/send-healthcheck", methods=["POST"])
def send_healthcheck():
    data = request.get_json()
    to_addr = data.get("to", EMAIL_TO).strip()
    html_content = data.get("html", "")
    subject = f"Veeam Backup Health Report — {datetime.now().strftime('%Y-%m-%d')}"

    if not SMTP_HOST:
        return jsonify({"error": "SMTP not configured. Add SMTP_HOST, SMTP_USER, SMTP_PASS to config_final.env"}), 400
    if not to_addr:
        return jsonify({"error": "No recipient address provided"}), 400

    log.info(f"[ROUTE /send-healthcheck] sending to={to_addr}")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_addr
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(EMAIL_FROM, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
                s.ehlo()
                s.starttls()
                if SMTP_USER:
                    s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(EMAIL_FROM, [to_addr], msg.as_string())

        log.info(f"[ROUTE /send-healthcheck] sent to={to_addr}")
        return jsonify({"success": True, "message": f"Report sent to {to_addr}"})
    except Exception as e:
        log.error(f"[ROUTE /send-healthcheck] error={e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
