# BackupPulse — Veeam Backup AI Assistant

A conversational AI chatbot for Veeam Backup & Replication environments.  
Ask plain-English questions about your backup estate. Get instant answers from all your VBR servers in one place.

> **Privacy-first:** Runs 100% locally with LM Studio (phi-4-mini on CPU). No data leaves your network.

---

## The Problem

A company with 3–10 Veeam Backup & Replication servers has no single view.  
Every morning an engineer must:

1. Open VBR1 → Jobs → filter Failed → note results
2. Open VBR2 → same
3. Open VBR3 → same
4. Manually combine everything into an email

**20–40 minutes. Every. Single. Morning.**

BackupPulse replaces that with one question:

```
last 24 hours failed jobs
```

---

## Demo

| Chat | Health Report |
|---|---|
| Ask in plain English | One-click HTML report |
| Groups by Server → Job → Object | Add engineer remarks inline |
| Works with Groq, OpenRouter, or local LLM | Send to management via email |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser (UI)                          │
│                      chat.html                               │
│  ┌──────────┐  ┌─────────────┐  ┌────────────────────────┐  │
│  │ Chat box │  │Provider/    │  │  Health Report         │  │
│  │          │  │Model switch │  │  + Email Send          │  │
│  └────┬─────┘  └──────┬──────┘  └──────────┬─────────────┘  │
└───────┼───────────────┼────────────────────┼────────────────┘
        │ POST /chat    │ POST /switch-provider│ GET /healthcheck
        ▼               ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      Flask (app.py)                          │
│                                                              │
│  process_question()                                          │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 1. Greeting? → instant reply (no LLM call)            │  │
│  │ 2. Send question to LLM → get SQL                     │  │
│  │ 3. DIRECT: answer? → return directly                  │  │
│  │ 4. Run SQL on PostgreSQL → get rows                   │  │
│  │ 5. Local model? → _format_rows() in Python            │  │
│  │    Cloud model? → send rows to LLM → get explanation  │  │
│  │ 6. Log Q&A to logs/qa.jsonl                           │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────┬──────────────────────┘
                │                      │
    ┌───────────▼───────────┐  ┌───────▼──────────────────────┐
    │      LLM Router       │  │       PostgreSQL              │
    │   call_llm()          │  │                              │
    │                       │  │  failed_jobs_daily           │
    │  groq      → Groq API │  │  job_sessions                │
    │  openrouter→ OR  API  │  │  protected_vms               │
    │  gemini    → Gemini   │  │  repositories                │
    │  lmstudio  → localhost│  │  restore_points              │
    │  ollama    → localhost│  │  long_running_jobs           │
    └───────────────────────┘  └──────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Web framework** | Flask (Python) | HTTP server, routing, template rendering |
| **Frontend** | Vanilla HTML/CSS/JS | No framework dependency, single file |
| **Database** | PostgreSQL + psycopg2 | Stores all Veeam backup data |
| **LLM — Cloud** | Groq, OpenRouter, Gemini, OpenAI | Fast inference, free tiers available |
| **LLM — Local** | LM Studio, Ollama | Air-gapped, zero external calls |
| **Encryption** | Fernet (cryptography lib) | Symmetric encryption for API keys |
| **Logging** | Python RotatingFileHandler | 3 separate log files, auto-rotation |
| **Email** | smtplib (STARTTLS/SSL) | Health report delivery |

---

## Features

- **Natural language queries** — no SQL knowledge needed
- **Multi-server consolidation** — all VBR servers in one view
- **Live provider switching** — change LLM mid-session without restart
- **Live model switching** — swap models within a provider from the UI
- **100% local mode** — LM Studio + phi-4-mini, zero internet
- **Engineer remarks** — add action notes in plain English, AI parses them
- **One-click health report** — HTML email with failures, repos, and remarks
- **Read-only SQL** — write operations (UPDATE/DELETE/DROP) blocked at app level
- **Encrypted config** — all API keys and passwords encrypted with Fernet
- **3-tier logging** — app events, errors, and Q&A pairs in separate files

---

## Supported LLM Providers

| Provider | Type | Cost | Notes |
|---|---|---|---|
| **Groq** | Cloud | Free (rate limits) | Fastest inference, recommended for demo |
| **OpenRouter** | Cloud | Free models available | `openai/gpt-oss-120b:free`, `google/gemma-4-31b-it:free` |
| **LM Studio** | Local | Free | phi-3.5-mini or phi-4-mini on CPU — zero internet |
| **Ollama** | Local | Free | Same models via CLI |
| **Gemini** | Cloud | Free tier | Google's API |
| **OpenAI** | Cloud | Paid | GPT-4o, GPT-4o-mini |
| **GitHub Copilot** | Cloud | Subscription | GPT-4o backend |

---

## Project Structure

```
BackupPulse/
├── app.py                    # Main Flask application
├── config_final.env.example  # Config template (copy → config_final.env)
├── encrypt_config.py         # One-time key encryption tool
├── start.py                  # Start app in background (no console window)
├── stop.py                   # Stop app by port
├── db_setup.py               # Create and seed demo PostgreSQL database
├── requirements.txt
│
├── sql_prompt.txt            # LLM instructions: SQL generation (cloud)
├── sql_prompt_local.txt      # LLM instructions: SQL generation (local)
├── system_prompt.txt         # LLM instructions: answer formatting (cloud)
├── system_prompt_local.txt   # LLM instructions: answer formatting (local)
│
├── templates/
│   ├── chat.html             # Single-page chat UI
│   └── health_email.html     # HTML email template
│
└── logs/                     # Auto-created at runtime
    ├── app.log               # All system activity (startup, LLM calls, DB)
    ├── errors.log            # Errors and warnings only
    └── qa.jsonl              # Every Q&A pair — one JSON line per turn
```

**Files NOT in this repo (gitignored):**
```
secret.key          ← Fernet encryption key — keep private, back up separately
config_final.env    ← Real API keys and passwords
logs/               ← Runtime logs (may contain sensitive query data)
remarks.json        ← Runtime session data
```

---

## How It Works

### Two-step LLM pipeline (cloud mode)

```
User question
     │
     ▼
[LLM Call 1] SQL generation
  System: sql_prompt.txt (schema + rules)
  User:   "last 24 hours failed jobs"
     │
     ▼
Generated SQL → PostgreSQL → rows
     │
     ▼
[LLM Call 2] Explanation
  System: system_prompt.txt (formatting rules)
  User:   SQL + rows + original question
     │
     ▼
Human-readable answer → browser
```

### One-step pipeline (local mode)

```
User question
     │
     ▼
[LLM Call 1] SQL generation (phi-4-mini)
  System: sql_prompt_local.txt (simpler schema)
  User:   question
     │
     ▼
Generated SQL → PostgreSQL → rows
     │
     ▼
_format_rows() — Python formats result directly
(no second LLM call — small models struggle with explanation)
     │
     ▼
Structured answer → browser
```

### DIRECT: shortcut (cloud mode only)

If the question is not a data question (e.g. "what is Veeam?"), the LLM replies with `DIRECT: [answer]` instead of SQL. The app strips the prefix and returns the answer immediately — no database query needed.

---

## Security Design

| What | How |
|---|---|
| API keys | Fernet symmetric encryption. `ENC:` prefix in config. Decrypted in RAM at startup only. |
| Database password | Same encryption, same approach. |
| SQL injection | Not possible — the AI generates SQL, Python executes it, no user input reaches the DB directly. |
| Write operations | Blocked by keyword list: UPDATE, DELETE, INSERT, DROP, ALTER, TRUNCATE. |
| Local mode | Zero external calls. Question, SQL, and results never leave the machine. |
| DB user | Can be replaced with a read-only PostgreSQL user for production. |

**The only file that must stay private: `secret.key`**  
Back it up to a password manager. If lost, all encrypted values must be re-entered and re-encrypted.

---

## Demo vs Production

---

### Demo Setup — Single Machine

Everything runs on one laptop or desktop. Fake data generated by `db_setup.py`.  
Purpose: show the concept, test the UI, present to stakeholders.

```
┌──────────────────────────────────────────────────────────────────┐
│                      Your Laptop / Desktop                        │
│                                                                  │
│  ┌──────────────┐         ┌───────────────────────────────────┐  │
│  │   Browser    │         │         Flask App (app.py)        │  │
│  │              │─────────▶         port 5000                 │  │
│  │ localhost    │◀─────────         start.py / stop.py        │  │
│  │    :5000     │         └──────────┬──────────────┬─────────┘  │
│  └──────────────┘                   │              │             │
│                                     │              │             │
│                          ┌──────────▼───┐  ┌───────▼──────────┐ │
│                          │  PostgreSQL   │  │   LLM Choice     │ │
│                          │  (local)      │  │                  │ │
│                          │               │  │  Option A:       │ │
│                          │  veeam_demo   │  │  LM Studio       │ │
│                          │  (fake data   │  │  phi-4-mini      │ │
│                          │  from         │  │  localhost:1234  │ │
│                          │  db_setup.py) │  │  ← NO INTERNET   │ │
│                          │               │  │                  │ │
│                          │  3 VBR servers│  │  Option B:       │ │
│                          │  5 jobs       │  │  Groq free API   │ │
│                          │  30 days data │  │  ← uses internet │ │
│                          └───────────────┘  └──────────────────┘ │
│                                                                  │
│  secret.key + config_final.env stay on this machine only         │
└──────────────────────────────────────────────────────────────────┘
```

**Demo data highlights (`db_setup.py` creates):**
- 3 VBR servers: `VBR-PROD`, `VBR-DR`, `VBR-BRANCH`
- 5 backup jobs across servers
- 30 days of backup history
- "Bad" objects that always fail: `WApp03`, `LApp02`, `WAgt03`, `NAS-SRV01`
- 4 repositories with varying capacity
- Restore points for each protected object

**Demo limitations:**
- Data is static fake data — not from a real Veeam environment
- Single user (no authentication layer)
- No VBR sync agent (data loaded once by db_setup.py)
- LM Studio must run manually before starting the app

---

### Production Setup — Dedicated Reporting Server

Real data. Multi-user. Air-gapped AI. All inside your network.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                          COMPANY INTERNAL NETWORK                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐            ║
║  │  VBR Server 1   │   │  VBR Server 2   │   │  VBR Server 3   │            ║
║  │  (HQ)           │   │  (DR Site)      │   │  (Branch)       │            ║
║  │                 │   │                 │   │                 │            ║
║  │  Veeam B&R      │   │  Veeam B&R      │   │  Veeam B&R      │            ║
║  │  SQL Server /   │   │  SQL Server /   │   │  SQL Server /   │            ║
║  │  PostgreSQL     │   │  PostgreSQL     │   │  PostgreSQL     │            ║
║  │  (Veeam's own   │   │  (Veeam's own   │   │  (Veeam's own   │            ║
║  │   internal DB)  │   │   internal DB)  │   │   internal DB)  │            ║
║  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘            ║
║           │                     │                     │                     ║
║           └─────────────────────┼─────────────────────┘                     ║
║                                 │                                            ║
║                    Sync Agent (scheduled, runs every 1h)                     ║
║                    Options:                                                  ║
║                    • Veeam REST API  (recommended)                          ║
║                    • Direct DB read (read-only SA user)                     ║
║                    • PowerShell + Veeam cmdlets export to CSV               ║
║                                 │                                            ║
║                                 ▼                                            ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │                      Reporting Server                                 │   ║
║  │                (Windows Server / Linux VM — 8GB RAM, 4 CPU)          │   ║
║  │                                                                      │   ║
║  │  ┌─────────────────────┐    ┌──────────────────────────────────────┐ │   ║
║  │  │   BackupPulse        │    │          LM Studio                   │ │   ║
║  │  │   Flask App          │◀──▶│          phi-4-mini (Q4_K_M)         │ │   ║
║  │  │   port 5000          │    │          localhost:1234               │ │   ║
║  │  │                      │    │                                      │ │   ║
║  │  │  start.py / stop.py  │    │   ← Runs on CPU, no GPU needed       │ │   ║
║  │  │  logs/ → 3 log files │    │   ← Zero internet, air-gapped        │ │   ║
║  │  └──────────┬───────────┘    └──────────────────────────────────────┘ │   ║
║  │             │                                                          │   ║
║  │  ┌──────────▼───────────────────────────────────────────────────────┐ │   ║
║  │  │              PostgreSQL (Consolidated Backup DB)                  │ │   ║
║  │  │                                                                  │ │   ║
║  │  │   failed_jobs_daily  │  job_sessions  │  protected_vms           │ │   ║
║  │  │   repositories       │  restore_points│  long_running_jobs       │ │   ║
║  │  │                                                                  │ │   ║
║  │  │   Read-only app user (backuppulse_ro) — cannot modify data       │ │   ║
║  │  └──────────────────────────────────────────────────────────────────┘ │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                 │                                            ║
║                   Internal HTTP (port 5000 or behind nginx)                  ║
║                                 │                                            ║
║  ┌──────────────────────────────┼──────────────────────────────────────┐    ║
║  │              Engineer / Manager Workstations                         │    ║
║  │                              │                                      │    ║
║  │   ┌────────────┐  ┌──────────▼─┐  ┌────────────┐  ┌────────────┐   │    ║
║  │   │ Engineer 1 │  │ Engineer 2 │  │ Engineer 3 │  │  Manager   │   │    ║
║  │   │  Browser   │  │  Browser   │  │  Browser   │  │  Browser   │   │    ║
║  │   │ (Chrome/   │  │ (Chrome/   │  │ (Chrome/   │  │ (receives  │   │    ║
║  │   │  Edge)     │  │  Edge)     │  │  Edge)     │  │  email     │   │    ║
║  │   └────────────┘  └────────────┘  └────────────┘  │  report)   │   │    ║
║  │                                                    └────────────┘   │    ║
║  └──────────────────────────────────────────────────────────────────────┘    ║
║                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │  Internal SMTP Relay  →  Manager inbox (HTML health report email)    │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                                                              ║
║  INTERNET ACCESS: BLOCKED — all AI runs inside the network                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

### Demo vs Production — Side by Side

| | Demo | Production |
|---|---|---|
| **Data source** | Fake data from `db_setup.py` | Sync agent pulling from real VBR servers |
| **Sync frequency** | One-time setup | Scheduled (every 1h via Task Scheduler or cron) |
| **Number of users** | 1 (you) | Multiple engineers + managers |
| **Authentication** | None | Windows Auth / reverse proxy (nginx + AD) |
| **LLM provider** | LM Studio (local) or Groq free tier | LM Studio (air-gapped) or private API |
| **LLM model** | phi-4-mini 3.8B Q4 — lightweight, CPU-only | llama-3.3-70B or mistral-22B — much smarter answers |
| **Hardware for AI** | Laptop CPU, ~3 GB RAM for model | Dedicated server CPU/GPU, 16–48 GB RAM |
| **Answer quality** | Good for common queries | Better reasoning, handles complex questions |
| **Server** | Your laptop | Dedicated VM (Windows Server or Linux) |
| **DB user** | postgres (admin) | `backuppulse_ro` (read-only user) |
| **Port exposure** | localhost:5000 | Internal network only, optionally behind nginx |
| **secret.key** | Stored on laptop | Stored on reporting server, backed up to password manager |
| **Logs** | logs/ on your machine | logs/ on reporting server, forwarded to SIEM optionally |

---

### Model Selection — Demo vs Production

#### Demo — Lightweight (runs on any laptop)

| Model | Size | RAM needed | Speed | Best for |
|---|---|---|---|---|
| `phi-4-mini-instruct` Q4 | 3.8B | ~3 GB | ~10 tok/s on i5 CPU | **Recommended for demo** |
| `phi-3.5-mini-instruct` Q4 | 3.8B | ~2.5 GB | ~12 tok/s on i5 CPU | Slightly faster, slightly weaker |
| Groq `llama-3.1-8b-instant` | Cloud | 0 GB local | ~200 tok/s | Good demo with internet |
| OpenRouter `openai/gpt-oss-20b:free` | Cloud | 0 GB local | fast | Free, no local install needed |

**Why lightweight for demo:**  
You are presenting from a laptop. Small models still generate correct SQL for the common backup questions (failed jobs, capacity, restore points). The answers are clear enough to show the concept. Speed matters more than depth when presenting live.

---

#### Production — Capable (runs on a server)

| Model | Size | RAM / VRAM needed | Speed | Best for |
|---|---|---|---|---|
| `llama-3.3-70b-instruct` Q4 | 70B | ~42 GB RAM (CPU) | ~4 tok/s CPU | **Best CPU-only prod model** |
| `mistral-22b-instruct` Q4 | 22B | ~14 GB RAM (CPU) | ~10 tok/s CPU | Good balance of quality + speed |
| `qwen2.5-14b-instruct` Q4 | 14B | ~9 GB RAM (CPU) | ~15 tok/s CPU | Excellent at structured output |
| `llama-3.3-70b-instruct` Q4 | 70B | ~24 GB VRAM (GPU) | ~60 tok/s | Fast GPU option (RTX 3090/4090) |
| `llama-3.1-405b` via private API | 405B | Hosted externally | ~100 tok/s | Maximum quality, needs API contract |

**Why bigger models in production:**
- Engineers ask harder questions: "which servers have the most failures this month compared to last month?"
- Bigger models handle multi-table joins, date arithmetic, and edge cases better
- Server hardware can support it — a dedicated VM with 32–64 GB RAM runs 70B models comfortably on CPU
- With a GPU (RTX 3090 or A100), 70B runs fast enough to feel instant
- No rate limits — your own hardware, your own pace

**Production recommendation:**  
Start with `qwen2.5-14b-instruct` Q4 — fits in 9 GB RAM, runs on a standard server CPU, and handles complex SQL and explanations very well. Upgrade to 70B if the server has 40+ GB RAM available.

```
Demo hardware:    i5 CPU, 16 GB RAM  →  phi-4-mini (3.8B Q4)
Prod hardware:    8-core CPU, 32 GB RAM  →  qwen2.5-14b (14B Q4)
Prod + GPU:       RTX 3090 24 GB VRAM  →  llama-3.3-70b (70B Q4)
```

---

### Production Data Flow (End to End)

```
 [Veeam B&R Server]                [Reporting Server]            [Engineers]
        │                                  │                          │
        │  Every 1 hour                   │                          │
        │  Sync Agent reads:              │                          │
        │  • Failed jobs                  │                          │
        │  • Job sessions                 │                          │
        │  • Repository capacity ─────────▶ INSERT/UPDATE            │
        │  • Protected VMs                │ PostgreSQL               │
        │  • Restore points               │                          │
        │                                 │                          │
        │                                 │  Engineer asks:          │
        │                                 │◀─"last 24h failed jobs"──│
        │                                 │                          │
        │                                 │  Flask → LM Studio       │
        │                                 │  LM Studio generates SQL │
        │                                 │  SQL → PostgreSQL        │
        │                                 │  Result → formatted      │
        │                                 │─────────────────────────▶│
        │                                 │  "3 failed backups:      │
        │                                 │   VBR-PROD / Prod_VM_Job │
        │                                 │   WApp03, LApp02..."     │
        │                                 │                          │
        │                                 │  Engineer adds remark:   │
        │                                 │◀─"WApp03 retry done"─────│
        │                                 │                          │
        │                                 │  AI parses → stored      │
        │                                 │                          │
        │                                 │  Click "Health Report"   │
        │                                 │◀─────────────────────────│
        │                                 │                          │
        │                                 │  HTML email generated    │
        │                                 │  SMTP → Manager inbox    │
        │                                 │─────────────────────────▶│
```

---

### Production Server Requirements

| Component | Minimum (small model) | Recommended (mid model) | Best (large model) |
|---|---|---|---|
| **Model** | phi-4-mini 3.8B Q4 | qwen2.5-14b Q4 | llama-3.3-70b Q4 |
| **CPU** | 4 cores | 8 cores | 16 cores |
| **RAM** | 8 GB | 16 GB | 48 GB |
| **GPU** | Not needed | Not needed | Optional (RTX 3090 = ~60 tok/s) |
| **Disk** | 40 GB | 60 GB | 120 GB |
| **OS** | Windows Server 2019+ or Ubuntu 22.04+ | Windows Server 2022 | Windows Server 2022 |
| **Python** | 3.10+ | 3.12 | 3.12 |
| **PostgreSQL** | 14+ | 16 | 16 |

> **Scaling rule:** RAM is the constraint. Each billion parameters at Q4 quantization uses roughly 0.6 GB RAM.  
> phi-4-mini 3.8B × 0.6 = **~2.5 GB**  
> qwen2.5-14b × 0.6 = **~9 GB**  
> llama-3.3-70b × 0.6 = **~42 GB**

> All models run on CPU only — no GPU required. A GPU dramatically improves speed but is not mandatory.

---

### What Still Needs to Be Built for Production

This repo provides the **reporting layer**. For a full production deployment you also need:

| Component | Status | Notes |
|---|---|---|
| BackupPulse UI + AI + logging | ✅ This repo | Complete |
| PostgreSQL schema | ✅ `db_setup.py` | Tables ready, needs real data |
| **Sync agent** | ⚠️ Not included | Must pull data from VBR APIs/DB into PostgreSQL |
| **Authentication** | ⚠️ Not included | Add nginx + Windows Auth or a login layer |
| **Read-only DB user** | ⚠️ Manual step | Create `backuppulse_ro` in PostgreSQL |
| **Scheduled sync** | ⚠️ Manual step | Windows Task Scheduler or cron |
| **nginx reverse proxy** | Optional | For HTTPS and proper URL (not :5000) |

The sync agent is environment-specific — it depends on how your VBR servers are configured (REST API, direct DB, or PowerShell export).

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/your-username/backuppulse.git
cd backuppulse
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config_final.env.example config_final.env
# Edit config_final.env — add your Groq API key at minimum
```

Get a free Groq API key at [console.groq.com](https://console.groq.com) — no credit card needed.

### 3. Encrypt your keys

```bash
python encrypt_config.py
```

This creates `secret.key` and replaces plain-text API keys with `ENC:...` in your config.

### 4. Set up the demo database

```bash
python db_setup.py
```

Creates `veeam_demo` database in PostgreSQL with 3 VBR servers, 5 jobs, 30 days of history.

### 5. Start

```bash
python start.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

```bash
python stop.py   # to stop
```

---

## Running 100% Locally (No Internet)

1. Download [LM Studio](https://lmstudio.ai)
2. Search and download `phi-4-mini-instruct` (Q4_K_M, ~2.5 GB)
3. Load the model → enable Local Server (port 1234)
4. In `config_final.env`:
   ```
   MODEL_PROVIDER=lmstudio
   ENABLED_PROVIDERS=lmstudio
   ```
5. Restart the app

Every question, every answer stays on your machine.

---

## Logging

| File | What's in it |
|---|---|
| `logs/app.log` | Startup, LLM call timings, DB query timings, route hits |
| `logs/errors.log` | Failed API calls, DB errors, exceptions |
| `logs/qa.jsonl` | Every question + answer pair as JSON |

```python
# Load Q&A pairs for analysis or training
import json
data = [json.loads(line) for line in open("logs/qa.jsonl", encoding="utf-8")]
```

---

## Example Questions

```
last 24 hours failed jobs
repository capacity
VMs missing backups
tell me about WApp03
long running jobs
how many restore points does WApp03 have?
```

```
# Engineer remarks (natural language — AI parses them)
Prod_VM_Job on VBR1 retry done completed
Linux backup job — case raised with vendor, INC123456
```

```
# Report actions
health report
clear all remarks
```

---

## License

MIT — free to use, modify, and distribute.

---

*Built for Veeam environments. Not affiliated with Veeam Software.*
