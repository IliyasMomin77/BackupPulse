import psycopg2
import requests
import os
import re
import json
import smtplib
import logging
import time
import hmac as _hmac
import hashlib
import base64
from logging.handlers import RotatingFileHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from pci_firewall import scrub as pci_scrub

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

_remarks = {}  # keyed by "vbr_server|job_name|failed_object_name" → remark text

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
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "phi-4-mini-instruct")

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

TEAMS_WEBHOOK_SECRET  = os.getenv("TEAMS_WEBHOOK_SECRET", "")
TEAMS_SNOW_OFFERING   = os.getenv("TEAMS_SNOW_OFFERING", "123")

# AWS Bedrock — private endpoint, no PCI data ever sent (scrubbed locally first)
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION            = os.getenv("AWS_REGION", "ap-south-1")
BEDROCK_MODEL         = os.getenv("BEDROCK_MODEL", "meta.llama3-1-8b-instruct-v1:0")
BEDROCK_GUARDRAIL_ID  = os.getenv("BEDROCK_GUARDRAIL_ID", "")


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
        "phi-4-mini-instruct",
    ],
    # AWS Bedrock (ap-south-1) — VPC-safe, PCI scrubbed before sending + Guardrails on AWS side
    "bedrock": [
        "meta.llama3-8b-instruct-v1:0",          # cheapest  ~$0.30/1M tokens
        "meta.llama3-70b-instruct-v1:0",         # smarter   ~$0.72/1M tokens
        "anthropic.claude-3-haiku-20240307-v1:0", # best quality ~$0.25/1M in
        "mistral.ministral-3-8b-instruct",        # fast alternative
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
        "bedrock":     BEDROCK_MODEL,
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

_LOCAL_PROVIDERS = {"lmstudio"}
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
    req_timeout = 120 if "localhost" in url or "127.0.0.1" in url else 30
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


def call_bedrock(prompt, system=None, max_tokens=500):
    try:
        import boto3
        client = boto3.client(
            service_name="bedrock-runtime",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
        kwargs = dict(
            modelId=BEDROCK_MODEL,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.1},
        )
        if system:
            kwargs["system"] = [{"text": system}]
        if BEDROCK_GUARDRAIL_ID:
            kwargs["guardrailConfig"] = {
                "guardrailIdentifier": BEDROCK_GUARDRAIL_ID,
                "guardrailVersion": "DRAFT",
                "trace": "enabled",
            }
        log.info(f"[BEDROCK] calling model={BEDROCK_MODEL} guardrail={'yes' if BEDROCK_GUARDRAIL_ID else 'no'}")
        t0 = time.perf_counter()
        resp = client.converse(**kwargs)
        duration = round(time.perf_counter() - t0, 2)
        reply = resp["output"]["message"]["content"][0]["text"].strip()
        log.info(f"[BEDROCK] OK duration={duration}s reply_len={len(reply)}")
        return reply
    except Exception as e:
        log.error(f"[BEDROCK] Error: {e}")
        raise


def call_llm(prompt, system=None, max_tokens=500):
    # PCI firewall — scrub user input before sending to any provider
    prompt, redacted = pci_scrub(prompt)
    if redacted:
        log.warning(f"[PCI_FIREWALL] Scrubbed before LLM call: {redacted}")

    if MODEL_PROVIDER == "bedrock":
        return call_bedrock(prompt, system, max_tokens)
    elif MODEL_PROVIDER == "gemini":
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



def _log_qa(question, answer, duration, provider, sql=None, row_count=None):
    entry = {
        "ts":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "answer":   answer,
        "provider": provider,
        "duration": duration,
    }
    if sql:
        entry["sql"] = sql
    if row_count is not None:
        entry["row_count"] = row_count
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
    sql   = ""
    rows  = []

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

    elif MODEL_PROVIDER in _LOCAL_PROVIDERS:
        from local_query import FAILED_KW, extract_sql, inject_date_filter, format_rows
        if FAILED_KW.search(question):
            raw_sql = call_llm(question, load_sql_prompt(), max_tokens=200)
            sql = inject_date_filter(extract_sql(raw_sql))
            if sql:
                rows = run_query(sql)
                reply = format_rows(question, rows) if rows else "No failed jobs found for that period."
            else:
                reply = "⚠️ Could not generate query. Switch to Groq or OpenRouter for better results."
            log.info(f"[CHAT] local failed-jobs done — {round(time.perf_counter()-t0,3)}s")
        else:
            reply = "This query works better on a cloud model. Switch to **Groq** or **OpenRouter** using the Provider dropdown above for full analysis."
            log.info(f"[CHAT] local — non-failed query, returning static reply")

    else:
        # Cloud providers: full SQL generation + LLM explain
        sql_user = (
            "Question: " + question + "\n\n"
            "INSTRUCTION: If this requires database data, respond with a valid PostgreSQL SELECT query ONLY. "
            "No explanation, no prose, no markdown — just the raw SQL starting with SELECT.\n"
            "If it is a greeting or general knowledge question (not about backup data), "
            "reply with DIRECT: followed by a brief answer."
        )
        raw_sql = call_llm(sql_user, load_sql_prompt(), max_tokens=300)
        sql_clean = re.sub(r"```sql|```", "", raw_sql).strip()
        if sql_clean.upper().startswith("DIRECT:"):
            direct_body = sql_clean[7:].strip()
            if re.search(r'\bSELECT\b', direct_body, re.IGNORECASE):
                # Model misclassified a data question — extract the SQL and run it
                sql_clean = direct_body
                log.info(f"[CHAT] DIRECT contained SQL — rerouting to query path")
            else:
                reply = direct_body
                log.info(f"[CHAT] DIRECT answer — {round(time.perf_counter()-t0,3)}s")

        if not reply and not raw_sql:
            reply = "The AI did not return a response. Please try again."
            log.warning("[CHAT] LLM returned None/empty for SQL generation")

        if not reply:
            sql = sql_clean
            if not sql.upper().startswith("SELECT"):
                m = re.search(r"(SELECT\b.+)", sql, re.IGNORECASE | re.DOTALL)
                sql = m.group(1).strip() if m else ""

            if not sql:
                # LLM returned plain text instead of SQL — use it as a direct answer
                reply = raw_sql.strip() or "I couldn't generate a query for that. Please try rephrasing."
                log.info(f"[CHAT] LLM returned text not SQL — used as direct reply")
            elif any(w in sql.lower() for w in ["update", "delete", "insert", "drop", "alter", "truncate"]):
                log.warning(f"[CHAT] blocked write SQL attempt — {sql[:80]}")
                reply = "I can only read backup data. Write operations are not allowed."
            else:
                rows = run_query(sql)
                if not rows:
                    log.info(f"[CHAT] query returned 0 rows — {round(time.perf_counter()-t0,3)}s")
                    reply = "I checked the database and there's nothing to report here — either everything looks clean or this data isn't available. You might want to check with your Backup Administrator."
                else:
                    # Map each unique error to its past resolution
                    err_to_fix = {}
                    for row in rows:
                        err = row.get("failure_message", "")
                        if err and err not in err_to_fix:
                            match = _find_past_resolution(err)
                            if match:
                                err_to_fix[err] = match
                    # Build inline resolution hints so LLM knows which fix belongs to which error
                    fix_hints = ""
                    if err_to_fix:
                        fix_hints = "\n\n[PAST RESOLUTIONS — show each fix directly after the matching error line]\n"
                        for err, fix in err_to_fix.items():
                            fix_hints += f"Error: {err[:80]}\n{fix}\n"
                    explain_prompt = (
                        "[QUERY RESULTS]\n" + sql + "\n" + str(rows[:50])
                        + "\n\n[USER QUESTION]\n" + question
                        + fix_hints
                    )
                    reply = call_llm(explain_prompt, load_system_prompt(), max_tokens=800)
                    log.info(f"[CHAT] cloud explain done rows={len(rows)} — {round(time.perf_counter()-t0,3)}s")

    _log_qa(question, reply, round(time.perf_counter() - t0, 3), MODEL_PROVIDER,
            sql=sql or None, row_count=len(rows) if rows else None)
    return reply


# ============================================================
# HEALTH CHECK EMAIL
# ============================================================

@app.template_filter('dt')
def _filter_dt(value):
    return str(value)[:16] if value else ''


def generate_health_email(failed_24h, critical_repos, long_running, zero_data, remarks=None):
    if not isinstance(remarks, dict):
        remarks = {}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_issues = bool(failed_24h or critical_repos or long_running or zero_data)
    return render_template(
        'health_email.html',
        ts=ts,
        overall="CRITICAL ISSUES FOUND" if has_issues else "ALL SYSTEMS HEALTHY",
        overall_bg="#DC2626" if has_issues else "#16A34A",
        f_cls="red"    if failed_24h    else "green",
        r_cls="red"    if critical_repos else "green",
        l_cls="yellow" if long_running   else "green",
        z_cls="yellow" if zero_data      else "green",
        failed_24h=failed_24h,
        critical_repos=critical_repos,
        long_running=long_running,
        zero_data=zero_data,
        remarks=remarks,
    )


def _fetch_health_data():
    from health_queries import (FAILED_JOBS_24H, CRITICAL_REPOS,
                                LONG_RUNNING_JOBS, ZERO_SIZE_JOBS)
    failed_24h     = run_query(FAILED_JOBS_24H)
    critical_repos = run_query(CRITICAL_REPOS)
    long_running   = run_query(LONG_RUNNING_JOBS)
    zero_data      = run_query(ZERO_SIZE_JOBS)
    return failed_24h, critical_repos, long_running, zero_data


# ============================================================
# ROUTES
# ============================================================

def _configured_providers():
    """Return providers to show in the UI — controlled by ENABLED_PROVIDERS in config."""
    enabled = {p.strip() for p in os.getenv("ENABLED_PROVIDERS", "groq,openrouter,lmstudio").split(",")}
    order   = ["groq", "openrouter", "lmstudio", "gemini", "openai", "copilot", "bedrock"]
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
    global GROQ_MODEL, OPENROUTER_MODEL, GEMINI_MODEL, OPENAI_MODEL, COPILOT_MODEL, LMSTUDIO_MODEL, BEDROCK_MODEL
    data = request.get_json()
    provider = data.get("provider", "").strip().lower()
    model    = data.get("model",    "").strip()
    valid = {"groq", "openrouter", "gemini", "openai", "copilot", "lmstudio", "bedrock"}
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
        elif provider == "bedrock":    BEDROCK_MODEL    = model


    if provider in _LOCAL_PROVIDERS:
        _SYSTEM_PROMPT = _load_file("system_prompt_local.txt", "You are a Veeam backup assistant. Explain the results clearly.")
        _SQL_PROMPT    = _load_file("sql_prompt_local.txt",    "Return a PostgreSQL SELECT query only. No explanation.")
    else:
        _SYSTEM_PROMPT = _load_file("system_prompt.txt", "You are VeeamBot, a Veeam Backup expert. Speak naturally.")
        _SQL_PROMPT    = _load_file("sql_prompt.txt",    "Generate a PostgreSQL SELECT query. Return SQL only.")

    active = _get_active_model()
    log.info(f"[SWITCH] {old_provider}/{old_model} -> {provider}/{active}")
    return jsonify({"success": True, "provider": provider, "model": active})


@app.route("/ping-provider", methods=["POST"])
def ping_provider():
    """Quick connectivity test for local providers (lmstudio)."""
    data     = request.get_json()
    provider = data.get("provider", MODEL_PROVIDER).strip().lower()
    model    = data.get("model", _get_active_model()).strip()

    url_map = {
        "lmstudio": LMSTUDIO_URL,
    }
    url = url_map.get(provider)
    if not url:
        return jsonify({"error": f"Ping only supported for local providers (lmstudio), got: {provider}"}), 400

    payload = {
        "model":       model,
        "messages":    [{"role": "user", "content": "hi"}],
        "max_tokens":  5,
        "temperature": 0,
    }
    try:
        import requests as _req
        t0 = time.perf_counter()
        r = _req.post(url, json=payload, timeout=90)
        ms = round((time.perf_counter() - t0) * 1000)
        if r.status_code == 200:
            log.info(f"[PING] {provider}/{model} OK {ms}ms")
            return jsonify({"ok": True, "ms": ms, "model": model})
        else:
            body = r.text[:200]
            log.warning(f"[PING] {provider} HTTP {r.status_code}: {body}")
            return jsonify({"ok": False, "error": f"HTTP {r.status_code}: {body}"}), 200
    except Exception as e:
        log.warning(f"[PING] {provider} failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 200


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
        _TEST_KEYWORDS = ("test", "testing", "ignore", "dummy", "fake", "sample", "trial")
        is_test = any(kw in resolution_notes.lower() for kw in _TEST_KEYWORDS)
        if not is_test:
            _log_incident_resolution(description or inc_number, resolution_notes)
        else:
            log.info(f"[ROUTE /resolve-incident] skipping log — test resolution for {inc_number}")
        return jsonify({"success": True, "message": f"✅ Incident **{inc_number}** resolved successfully in ServiceNow."})
    except Exception as e:
        log.error(f"[ROUTE /resolve-incident] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/healthcheck")
def healthcheck():
    log.info("[ROUTE /healthcheck] generating health report")
    t0 = time.perf_counter()
    try:
        failed_24h, critical_repos, long_running, zero_data = _fetch_health_data()
        log.info(f"[ROUTE /healthcheck] failed={len(failed_24h)} repos={len(critical_repos)} long={len(long_running)} zero={len(zero_data)} remarks={len(_remarks)} — {round(time.perf_counter()-t0,3)}s")
        return jsonify({
            "email_html": generate_health_email(failed_24h, critical_repos, long_running, zero_data, dict(_remarks)),
            "counts": {
                "failed":      len(failed_24h),
                "repos":       len(critical_repos),
                "long_running": len(long_running),
                "zero_data":   len(zero_data),
                "remarks":     len(_remarks)
            }
        })
    except Exception as e:
        log.error(f"[ROUTE /healthcheck] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/failed-jobs-list")
def failed_jobs_list():
    try:
        failed_24h, _, _, _ = _fetch_health_data()
        jobs = []
        for r in failed_24h[:60]:
            vm_key  = f"{r['vbr_server']}|{r['job_name']}|{r['failed_object_name']}"
            job_key = f"JOB|{r['vbr_server']}|{r['job_name']}"
            jobs.append({
                "key":         vm_key,
                "job_key":     job_key,
                "backup_date": str(r.get("backup_date", "")),
                "vbr_server":  r.get("vbr_server", ""),
                "job_name":    r.get("job_name", ""),
                "object":      r.get("failed_object_name", ""),
                "remark":      _remarks.get(vm_key, ""),
                "job_remark":  _remarks.get(job_key, ""),
            })
        return jsonify({"jobs": jobs})
    except Exception as e:
        log.error(f"[ROUTE /failed-jobs-list] error={e}")
        return jsonify({"error": str(e)}), 500


@app.route("/set-remarks", methods=["POST"])
def set_remarks():
    data = request.get_json()
    new = data.get("remarks", {})
    for k, v in new.items():
        if v.strip():
            _remarks[k] = v.strip()
        elif k in _remarks:
            del _remarks[k]
    _save_remarks(_remarks)
    log.info(f"[ROUTE /set-remarks] total={len(_remarks)} remarks")
    failed_24h, critical_repos, long_running, zero_data = _fetch_health_data()
    email_html = generate_health_email(failed_24h, critical_repos, long_running, zero_data, dict(_remarks))
    return jsonify({"success": True, "html": email_html, "count": len(_remarks)})


@app.route("/clear-remarks", methods=["POST"])
def clear_remarks():
    count = len(_remarks)
    _remarks.clear()
    _save_remarks(_remarks)
    log.info(f"[ROUTE /clear-remarks] cleared {count} remark(s)")
    failed_24h, critical_repos, long_running, zero_data = _fetch_health_data()
    email_html = generate_health_email(failed_24h, critical_repos, long_running, zero_data, {})
    return jsonify({"success": True, "html": email_html, "message": "All remarks cleared."})


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
        _remarks.clear()
        _save_remarks(_remarks)
        log.info("[ROUTE /send-healthcheck] remarks cleared after send")
        return jsonify({"success": True, "message": f"Report sent to {to_addr}"})
    except Exception as e:
        log.error(f"[ROUTE /send-healthcheck] error={e}")
        return jsonify({"error": str(e)}), 500


# ============================================================
# TEAMS BOT — Outgoing Webhook
# ============================================================

def _verify_teams_hmac(auth_header, body_bytes):
    """Verify Teams HMAC-SHA256 signature. Returns True in dev if secret not configured."""
    if not TEAMS_WEBHOOK_SECRET:
        return True
    if not auth_header.startswith("HMAC "):
        return False
    try:
        token = base64.b64decode(TEAMS_WEBHOOK_SECRET)
        mac = _hmac.new(token, body_bytes, hashlib.sha256)
        expected = "HMAC " + base64.b64encode(mac.digest()).decode()
        return _hmac.compare_digest(auth_header, expected)
    except Exception:
        return False


def _teams_groq(prompt, system=None, max_tokens=400):
    """Always call Groq for Teams — independent of the global MODEL_PROVIDER."""
    return call_openai_compatible(prompt, system, GROQ_URL, GROQ_API_KEY, GROQ_MODEL, max_tokens)


def _run_query_params(sql, params=()):
    """Parameterised DB query — safe for values extracted from user messages."""
    t0 = time.perf_counter()
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        log.info(f"[DB/TEAMS] rows={len(rows)} duration={round(time.perf_counter()-t0,3)}s")
        return rows
    except Exception as e:
        log.error(f"[DB/TEAMS] FAILED error={e}")
        raise
    finally:
        conn.close()


_TEAMS_SKIP_WORDS = {
    "is", "in", "the", "a", "an", "for", "of", "on", "was", "check",
    "last", "backup", "status", "get", "add", "list", "all", "show",
    "please", "can", "you", "what", "this", "week", "report", "sla",
    "protected", "any", "has", "have", "how", "do", "i", "it", "my",
    "recent", "latest", "find", "server", "servers", "vm", "vms",
    "machine", "machines", "starting", "with", "from", "backuppulse",
    "backuppulsebot", "good", "bad", "if", "not", "did", "does",
    "successful", "successfully", "failed", "fine", "okay", "ok",
    "when", "where", "which", "like", "just", "now", "were", "been",
    "run", "ran", "working", "complete", "completed", "job", "jobs",
}


def _extract_machine(text):
    """Extract the most likely server/VM name from a Teams message.
    Prefers tokens containing digits (e.g. LApp04, WAgt03) as these match
    real server naming conventions over common English words."""
    tokens = re.findall(r'\b([A-Za-z][A-Za-z0-9\-_]{2,}(?:\*)?)\b', text)
    candidates = [t for t in tokens if t.lower() not in _TEAMS_SKIP_WORDS]
    # Prefer tokens with a digit — real server names almost always have one
    with_digit = [t for t in candidates if re.search(r'\d', t)]
    return with_digit[0] if with_digit else (candidates[0] if candidates else None)


def _teams_check_protected(machine):
    rows = _run_query_params(
        "SELECT object_name, object_type, job_name, vbr_server, last_successful_backup "
        "FROM protected_vms WHERE LOWER(object_name) = LOWER(%s) LIMIT 1",
        (machine,)
    )
    if not rows:
        return (
            f"⚠️ **{machine}** is **not found** in the backup system.\n\n"
            f"To get it added, raise a ServiceNow request:\n"
            f"**Offering #{TEAMS_SNOW_OFFERING} — Add Server to Backup**\n\n"
            f"Provide the server name, OS type, and estimated data size. "
            f"The backup team will configure it within 2 business days."
        )
    r = rows[0]
    last = r.get("last_successful_backup")
    last_str = last.strftime("%Y-%m-%d %H:%M") if last else "No record"
    return (
        f"✅ **{r['object_name']}** is protected.\n"
        f"- **Type:** {r.get('object_type', 'VM')}\n"
        f"- **Job:** {r.get('job_name', '—')}\n"
        f"- **VBR Server:** {r.get('vbr_server', '—')}\n"
        f"- **Last Successful Backup:** {last_str}"
    )


def _teams_last_backup(machine):
    protected = _run_query_params(
        "SELECT last_successful_backup FROM protected_vms "
        "WHERE LOWER(object_name) = LOWER(%s) LIMIT 1",
        (machine,)
    )
    if not protected:
        return (
            f"⚠️ **{machine}** is not registered in the backup system.\n"
            f"Raise SNOW Offering #{TEAMS_SNOW_OFFERING} to get it added."
        )
    last = protected[0].get("last_successful_backup")
    last_str = last.strftime("%Y-%m-%d %H:%M") if last else "No successful backup on record"

    failures = _run_query_params(
        "SELECT backup_date, job_name, failure_message FROM failed_jobs_daily "
        "WHERE LOWER(failed_object_name) = LOWER(%s) "
        "ORDER BY backup_date DESC LIMIT 3",
        (machine,)
    )
    lines = [
        f"**Backup status for {machine}:**",
        f"- Last successful backup: **{last_str}**",
    ]
    if failures:
        lines.append("\nRecent failures:")
        for r in failures:
            msg = (r.get("failure_message") or "")[:80]
            lines.append(f"  ❌ {r['backup_date']} — {r['job_name']}: {msg}")
    else:
        lines.append("- No recent failures ✅")
    return "\n".join(lines)



def _teams_list_vms(pattern):
    if pattern:
        like = pattern.replace("*", "%")
        rows = _run_query_params(
            "SELECT object_name, object_type, job_name, vbr_server FROM protected_vms "
            "WHERE LOWER(object_name) LIKE LOWER(%s) ORDER BY object_name LIMIT 30",
            (like,)
        )
    else:
        rows = _run_query_params(
            "SELECT object_name, object_type, job_name, vbr_server FROM protected_vms "
            "ORDER BY object_name LIMIT 30"
        )
    if not rows:
        pat_str = f" matching *{pattern}*" if pattern else ""
        return f"No protected VMs found{pat_str}."
    pat_str = f" matching *{pattern}*" if pattern else ""
    lines = [f"**Protected VMs{pat_str} ({len(rows)}):**\n"]
    for r in rows:
        lines.append(f"  • **{r['object_name']}** ({r.get('object_type','VM')}) — {r.get('job_name','')}")
    return "\n".join(lines)


def _teams_sla_report():
    rows = _run_query_params(
        """
        SELECT
            COUNT(DISTINCT p.object_name)                                         AS total_protected,
            COUNT(DISTINCT f.failed_object_name)                                  AS total_failed,
            ROUND(100.0 * (COUNT(DISTINCT p.object_name)
                         - COUNT(DISTINCT f.failed_object_name))
                  / NULLIF(COUNT(DISTINCT p.object_name), 0), 1)                 AS sla_pct
        FROM protected_vms p
        LEFT JOIN failed_jobs_daily f
            ON  LOWER(p.object_name) = LOWER(f.failed_object_name)
            AND f.backup_date >= CURRENT_DATE - 7
        """
    )
    if not rows:
        return "Unable to retrieve SLA data right now."
    r = rows[0]
    total  = int(r.get("total_protected") or 0)
    failed = int(r.get("total_failed") or 0)
    sla    = float(r.get("sla_pct") or 0)
    emoji  = "✅" if sla >= 95 else "⚠️" if sla >= 85 else "🔴"
    return (
        f"**{emoji} Backup SLA Report — Last 7 Days**\n\n"
        f"- Protected objects: **{total}**\n"
        f"- Successful: **{total - failed}**\n"
        f"- Failed: **{failed}**\n"
        f"- SLA: **{sla}%**"
    )


def _teams_guidance(machine):
    name = f"**{machine}**" if machine else "this server"
    return (
        f"To add {name} to the backup schedule:\n\n"
        f"1. Go to ServiceNow and raise a service request\n"
        f"2. Select **Offering #{TEAMS_SNOW_OFFERING} — Add Server to Backup**\n"
        f"3. Fill in: server name, OS type, data size estimate\n"
        f"4. The backup team will configure and confirm within 2 business days\n\n"
        f"_Questions? Contact your Backup Administrator._"
    )


def _teams_help():
    return (
        "👋 **BackupPulse Bot** — here's what I can do:\n\n"
        "- `is WApp01 in backup?` — check if a server is protected\n"
        "- `last backup of WAgt03` — last backup date and recent failures\n"
        "- `list VMs starting with web*` — find protected servers by name\n"
        "- `SLA report this week` — backup success rate for last 7 days\n"
        "- `how to add WServer01 to backup` — onboarding guide\n\n"
        "_You can also ask free-form backup questions and I'll query the database._"
    )


def _process_teams_message(text):
    q = text.lower().strip()

    # Help / greeting / empty /backuppulse
    if re.match(r'^(hi|hello|help|hey|\?)\s*$', q) or not q:
        return _teams_help()

    # "is X in backup?" / "is X protected?" / "backup status of X"
    if re.search(r'\b(is|check|was)\b.{0,40}\b(in backup|protected|backed up)\b'
                 r'|\bbackup\s+status\s+of\b', q):
        machine = _extract_machine(text)
        return _teams_check_protected(machine) if machine else \
               "Please mention the server name. Example: *is WApp01 in backup?*"

    # "last backup of X" / "recent backup X" / "backup history X"
    if re.search(r'\b(last|recent|latest)\b.{0,20}\bbackup\b'
                 r'|\bbackup.{0,20}\b(history|status)\b', q):
        machine = _extract_machine(text)
        return _teams_last_backup(machine) if machine else \
               "Please mention the server name. Example: *last backup of WApp01*"

    # "add X to backup" / "how to get X backed up" / "not in backup"
    if re.search(r'\b(add|get|put|register|onboard|enrol)\b.{0,40}\bbackup\b'
                 r'|\bnot in backup\b', q):
        return _teams_guidance(_extract_machine(text))

    # All data queries — Groq generates SQL, executes, Groq formats naturally
    try:
        # Step 1: Groq generates SQL
        raw = _teams_groq(text, system=load_sql_prompt(), max_tokens=300)
        sql_clean = re.sub(r"```sql|```", "", raw or "").strip()
        m = re.search(r"(SELECT\b[^;]+;?)", sql_clean, re.IGNORECASE | re.DOTALL)
        sql_clean = m.group(1).strip() if m else ""
        if not sql_clean:
            return _teams_help()

        # Step 2: Execute SQL
        rows = run_query(sql_clean)
        if not rows:
            return "No data found for that query."

        # Step 3: Groq formats results as natural language
        keys = list(rows[0].keys())
        data_lines = [", ".join(keys)]
        for r in rows[:40]:
            data_lines.append(", ".join(str(r.get(k, "")) for k in keys))
        if len(rows) > 40:
            data_lines.append(f"({len(rows) - 40} more rows not shown)")
        data_text = "\n".join(data_lines)

        fmt_system = (
            "You are BackupPulse, a Veeam Backup & Replication assistant in Microsoft Teams.\n"
            "You receive a user question and raw database results. Write a natural, conversational reply — "
            "like a helpful colleague, not a system dump.\n\n"
            "TONE & STYLE:\n"
            "- Answer the question directly. Lead with the key finding.\n"
            "- Speak naturally: 'Yes, the last backup for LApp04 was successful on 2026-06-15 at 13:56' not a table.\n"
            "- No preamble ('Sure!', 'Great question!', 'Based on the data...'). Get straight to the point.\n"
            "- No markdown headers (##). Use **bold** for server names, dates, and key numbers.\n"
            "- Keep under 250 words.\n\n"
            "CONTENT RULES:\n"
            "- Backup status: ✅ success/protected, ❌ failed/not found, ⚠️ warning or missing data.\n"
            "- 'Was the backup good/successful?' → check last_successful_backup timestamp. If recent, say yes with the date.\n"
            "- For lists: group by type (VMs / Agents / File Servers) with bullet points. Show server names clearly.\n"
            "- For counts: state the number first, then break down by type if available.\n"
            "- For SLA: state the percentage prominently, then protected vs failed counts.\n"
            "- For storage: mention used% and free space per repo.\n"
            "- For failures: name the server, job, and give a brief reason.\n"
            "- Timestamps: format as 'YYYY-MM-DD HH:MM' — no seconds.\n"
            "- If result is empty: say what wasn't found and suggest checking the server name spelling."
        )
        reply = _teams_groq(
            f"Question: {text}\n\nDatabase results:\n{data_text}",
            system=fmt_system,
            max_tokens=500
        )
        return reply or "No response from model."
    except Exception as e:
        log.error(f"[TEAMS] fallback error: {e}")
        return "Sorry, I couldn't process that. Type *help* to see what I can do."


@app.route("/api/teams", methods=["POST"])
def teams_webhook():
    body = request.get_data()
    auth = request.headers.get("Authorization", "")

    if not _verify_teams_hmac(auth, body):
        log.warning("[TEAMS] HMAC verification failed — possible spoofed request")
        return jsonify({"type": "message", "text": "❌ Unauthorized"}), 401

    data     = request.get_json(force=True) or {}
    raw_text = data.get("text", "")

    # Strip HTML tags (@mention wraps in <at>...</at>)
    text = re.sub(r'<[^>]+>', '', raw_text).strip()
    # Decode HTML entities Teams injects between the @mention and the message body
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text).replace('&gt;', '>').strip()
    # Strip bot name that remains after tag removal: "BackupPulseBot is X" → "is X"
    text = re.sub(r'^@?BackupPulseBot?\s*', '', text, flags=re.IGNORECASE).strip()
    # Strip /backuppulse command prefix
    text = re.sub(r'^/?backuppulsebot?\s*', '', text, flags=re.IGNORECASE).strip()

    log.info(f"[TEAMS] message={text[:120]!r}")

    if not text:
        return jsonify({"type": "message", "text": _teams_help()})

    try:
        t0    = time.perf_counter()
        reply = _process_teams_message(text)
        log.info(f"[TEAMS] replied in {round(time.perf_counter()-t0, 2)}s")
        return jsonify({"type": "message", "text": reply})
    except Exception as e:
        log.error(f"[TEAMS] error: {e}")
        return jsonify({"type": "message", "text": "⚠️ Something went wrong. Please try again."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
