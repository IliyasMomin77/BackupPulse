# BackupPulse — Veeam Backup AI Assistant

Ask plain-English questions about your Veeam backup estate. Get instant answers from all VBR servers in one place.

> **Privacy-first:** Runs 100% locally with LM Studio. No data leaves your network.

---

## The Problem

A company with 3–10 Veeam B&R servers has no single view.  
Every morning an engineer opens each server, filters failures, and manually combines everything into an email. **20–40 minutes. Every day.**

BackupPulse replaces that with one question: `last 24 hours failed jobs`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (UI)                          │
│  Chat box  │  Provider/Model switch  │  Health Report        │
└───────────────────┬─────────────────────────────────────────┘
                    │ POST /chat  /switch-provider  /healthcheck
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                      Flask (app.py)                          │
│  1. Greeting? → instant reply                                │
│  2. Send question to LLM → get SQL                           │
│  3. DIRECT: answer? → return immediately (no DB)             │
│  4. Run SQL on PostgreSQL → get rows                         │
│  5. Local model? → Python formats result                     │
│     Cloud model? → LLM explains result                       │
│  6. Log Q&A to logs/qa.jsonl                                 │
└───────────────┬──────────────────────┬──────────────────────┘
                │                      │
    ┌───────────▼───────────┐  ┌───────▼──────────────────┐
    │      LLM Router       │  │       PostgreSQL          │
    │  groq  / openrouter   │  │  failed_jobs_daily        │
    │  lmstudio / ollama    │  │  job_sessions             │
    │  gemini / openai      │  │  repositories             │
    └───────────────────────┘  └───────────────────────────┘
```

---

## Why PostgreSQL Sits Between Veeam and the LLM

- The LLM never has direct access to production Veeam databases
- Raw Veeam data is pulled into clean PostgreSQL tables — the LLM queries those
- If a query goes wrong, worst case is a bad SELECT on the reporting DB, not the production system
- As LLMs improve, this middle layer can eventually be removed

---

## Cloud vs Local — Two Different Pipelines

**Cloud (Groq, OpenRouter, Gemini):** Two LLM calls

```
Question → [LLM 1: generate SQL] → PostgreSQL → rows → [LLM 2: explain result] → answer
```

**Local (LM Studio, Ollama):** One LLM call

```
Question → [LLM: generate SQL] → PostgreSQL → rows → Python formats → answer
```

**Why the difference?**  
Small local models (phi-4-mini, 3.8B) running on a laptop CPU cannot reliably handle long system prompts or complex explanation tasks — pushing a large model on demo hardware causes slowdowns and poor output. So the local pipeline uses a shorter, simpler prompt and skips the second LLM call entirely. Python handles the formatting instead. Cloud models have no such constraint.

This also means two separate prompt files:

| File | Used by | Purpose |
|---|---|---|
| `sql_prompt.txt` + `system_prompt.txt` | Cloud | Full instructions, rich formatting rules |
| `sql_prompt_local.txt` + `system_prompt_local.txt` | Local | Shorter, simpler — matched to small model limits |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask (Python) |
| Frontend | Vanilla HTML/CSS/JS |
| Database | PostgreSQL + psycopg2 |
| LLM — Cloud | Groq, OpenRouter, Gemini, OpenAI |
| LLM — Local | LM Studio, Ollama |
| Encryption | Fernet (cryptography lib) |
| Logging | Python RotatingFileHandler |

---

## Features

- Natural language queries — no SQL knowledge needed
- Multi-server consolidation — all VBR servers in one view
- Live provider + model switching — no restart needed
- 100% local mode — LM Studio + phi-4-mini, zero internet
- Engineer remarks — add action notes, AI parses them
- One-click HTML health report → email to management
- Read-only SQL — UPDATE/DELETE/DROP blocked at app level
- Encrypted config — API keys stored with Fernet encryption
- 3-tier logging — app events, errors, and Q&A in separate files

---

## Supported Providers

| Provider | Type | Cost |
|---|---|---|
| **Groq** | Cloud | Free (rate limits) — recommended for demo |
| **OpenRouter** | Cloud | Free models available |
| **LM Studio** | Local | Free — zero internet |
| **Ollama** | Local | Free — zero internet |
| **Gemini / OpenAI / Copilot** | Cloud | Free tier / Paid |

---

## Demo vs Production

| | Demo | Production |
|---|---|---|
| Data | Fake data from `db_setup.py` | Sync agent from real VBR servers |
| Users | 1 | Multiple engineers + managers |
| Auth | None | Reverse proxy + Windows Auth |
| LLM | phi-4-mini 3.8B (CPU, ~3 GB RAM) | llama-3.3-70B or qwen2.5-14B on server |
| Hardware | Laptop | Dedicated VM, 16–48 GB RAM |
| DB user | postgres | Read-only `backuppulse_ro` user |

**Demo model:** phi-4-mini runs on any laptop CPU. Good enough for common queries when presenting live.  
**Production model:** Bigger models handle complex multi-table queries and date arithmetic better. `qwen2.5-14b` (Q4) fits in ~9 GB RAM and is a solid starting point.

> RAM rule: parameters × 0.6 GB ≈ RAM needed. `14B × 0.6 = ~9 GB`

---

## Project Structure

```
BackupPulse/
├── app.py                    # Main Flask application
├── config_final.env.example  # Config template
├── encrypt_config.py         # One-time key encryption tool
├── start.py / stop.py        # Start and stop app
├── db_setup.py               # Create and seed demo database
├── requirements.txt
├── sql_prompt.txt            # SQL instructions (cloud)
├── sql_prompt_local.txt      # SQL instructions (local)
├── system_prompt.txt         # Answer formatting (cloud)
├── system_prompt_local.txt   # Answer formatting (local)
└── templates/
    ├── chat.html
    └── health_email.html
```

**Gitignored (never commit):** `secret.key`, `config_final.env`, `logs/`, `remarks.json`

---

## Quick Start

```bash
git clone https://github.com/IliyasMomin77/BackupPulse.git
cd BackupPulse
pip install -r requirements.txt
cp config_final.env.example config_final.env
# Add your Groq API key — free at console.groq.com
python encrypt_config.py
python db_setup.py
python start.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## Running 100% Locally

1. Download [LM Studio](https://lmstudio.ai) → load `phi-4-mini-instruct` (Q4_K_M)
2. Enable Local Server (port 1234)
3. Set in `config_final.env`: `MODEL_PROVIDER=lmstudio`
4. Restart the app

Every question and answer stays on your machine.

---

## Logging

| File | Contents |
|---|---|
| `logs/app.log` | Startup, LLM timings, DB queries |
| `logs/errors.log` | Errors and warnings only |
| `logs/qa.jsonl` | Every Q&A pair as JSON — useful for future LLM training |

---

## Example Questions

```
last 24 hours failed jobs
repository capacity
VMs missing backups
how many restore points does WApp03 have?
health report
```

---

## What Still Needs Building for Production

| Component | Status |
|---|---|
| BackupPulse UI + AI + logging | ✅ This repo |
| PostgreSQL schema | ✅ `db_setup.py` |
| Sync agent (VBR → PostgreSQL) | ⚠️ Not included — environment-specific |
| Authentication layer | ⚠️ Not included |
| Read-only DB user | ⚠️ Manual step |

---

## License

MIT — free to use, modify, and distribute.

*Not affiliated with Veeam Software.*
