# BackupPulse — Veeam Backup AI Assistant

Ask plain-English questions about your Veeam backup estate. Get instant answers from all VBR servers in one place.

> **Flexible by design:** Works with any LLM — local (LM Studio), cloud (Groq, OpenRouter, Gemini, OpenAI), or secure enterprise (AWS Bedrock). Switch providers live from the UI without restarting.

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
│  1. PCI Firewall — strip card/SSN/PAN before any LLM call   │
│  2. Greeting? → instant reply                                │
│  3. Send question to LLM → get SQL                           │
│  4. DIRECT: answer? → return immediately (no DB)             │
│  5. Run SQL on PostgreSQL → get rows                         │
│  6. Local model? → Python formats result                     │
│     Cloud model? → LLM explains result                       │
│  7. Log Q&A to logs/qa.jsonl                                 │
└───────────────┬──────────────────────┬──────────────────────┘
                │                      │
    ┌───────────▼───────────┐  ┌───────▼──────────────────┐
    │      LLM Router       │  │       PostgreSQL          │
    │  groq  / openrouter   │  │  failed_jobs_daily        │
    │  lmstudio             │  │  job_sessions             │
    │  gemini / openai      │  │  repositories             │
    │  AWS Bedrock          │  │  protected_vms            │
    └───────────────────────┘  └───────────────────────────┘
```

---

## Why PostgreSQL Sits Between Veeam and the LLM

- The LLM never has direct access to production Veeam databases
- Raw Veeam data is pulled into clean PostgreSQL tables — the LLM queries those
- If a query goes wrong, worst case is a bad SELECT on the reporting DB, not the production system
- Read-only DB user (`veeam_bot`) — cannot INSERT, UPDATE, or DELETE by design

---

## Cloud vs Local — Two Different Pipelines

**Cloud (Groq, OpenRouter, Bedrock):** Two LLM calls

```
Question → [PCI Firewall] → [LLM 1: generate SQL] → PostgreSQL → rows → [LLM 2: explain result] → answer
```

**Local (LM Studio):** One LLM call

```
Question → [PCI Firewall] → [LLM: generate SQL] → PostgreSQL → rows → Python formats → answer
```

**Why the difference?**  
Small local models (phi-4-mini, 3.8B) running on a laptop CPU cannot reliably handle long system prompts or complex explanation tasks. So the local pipeline uses a shorter, simpler prompt and skips the second LLM call. Python handles formatting instead.

---

## Security Architecture

### PCI Firewall (Local — Zero Cost)

Every user message passes through `pci_firewall.py` **before any network call** — regardless of provider.

```
User message
     ↓
[pci_firewall.py]  ← runs on your machine
  strips: credit card numbers, CVV, SSN, IBAN, Indian PAN, Aadhaar
     ↓
[LLM Provider]  ← never sees raw PCI data
```

Patterns detected and masked:

| Data Type | Example | Masked As |
|---|---|---|
| Credit card | `4111 1111 1111 1111` | `[CARD_REDACTED]` |
| CVV / CVC | `CVV 123` | `[CVV_REDACTED]` |
| US SSN | `123-45-6789` | `[SSN_REDACTED]` |
| IBAN | `GB29 NWBK 6016 1331` | `[IBAN_REDACTED]` |
| Indian PAN | `ABCDE1234F` | `[PAN_REDACTED]` |
| Aadhaar | `2345 6789 0123` | `[AADHAAR_REDACTED]` |

### AWS Bedrock Guardrails (Second Layer)

When using the Bedrock provider, a second guardrail runs on the AWS side:

- **PII anonymization** — credit cards, emails, phone numbers masked at AWS level
- **Denied topics** — blocks non-IT questions (finance, medical, legal advice)
- **Prompt attack protection** — blocks jailbreak attempts

### Database Security

- App user (`veeam_bot`) is read-only — cannot modify any data
- SQL generation prompt explicitly blocks INSERT/UPDATE/DELETE/DROP
- Setup/admin operations use a separate `DB_SETUP_USER` (postgres)

---

## AWS Bedrock — Secure Network Deployment

For organizations with strict data governance, Bedrock can be deployed inside a VPC with no internet egress:

```
Private Subnet (app server)
        │
        │  No internet gateway
        │
   [VPC Interface Endpoint]   ← traffic stays inside AWS backbone
        │                        never touches public internet
        ▼
   AWS Bedrock (ap-south-1)
   bedrock-runtime.ap-south-1.amazonaws.com
```

Only the Bedrock endpoint URL needs to be whitelisted in your connectivity gateway. All other cloud providers (Groq, OpenRouter) go over the public internet and are automatically unavailable in a locked-down VPC — making Bedrock the right choice for air-gapped enterprise deployments.

**Available models (ap-south-1 / Mumbai):**

| Model | Cost | Notes |
|---|---|---|
| `meta.llama3-8b-instruct-v1:0` | ~$0.30/1M tokens | Cheapest |
| `meta.llama3-70b-instruct-v1:0` | ~$0.72/1M tokens | Smarter |
| `anthropic.claude-3-haiku-20240307-v1:0` | ~$0.25/1M in | Best quality |
| `mistral.ministral-3-8b-instruct` | ~$0.10/1M tokens | Fast |

---

## Microsoft Teams Integration

BackupPulse includes an outgoing webhook bot (**BackupPulseBot**) for Microsoft Teams.

```
Teams user: @BackupPulseBot Recent failed jobs
                    ↓
         ngrok (or internal URL)
                    ↓
         /teams-webhook endpoint
         • HMAC-SHA256 verification
         • HTML entity decoding
         • Bot name stripping
         • Machine name extraction
                    ↓
         Same SQL engine as chatbot
                    ↓
         Natural language reply → Teams
```

**Setup:**
1. Teams Admin → Apps → Outgoing webhooks → create `BackupPulseBot`
2. Paste callback URL: `https://<your-domain>/teams-webhook`
3. Copy the security token → `TEAMS_WEBHOOK_SECRET` in config
4. Run `python encrypt_config.py` to encrypt

**Example queries in Teams:**
```
@BackupPulseBot What failed today?
@BackupPulseBot SLA report for this week
@BackupPulseBot Check last backup for LApp04
@BackupPulseBot Objects not backed up in 2 days
```

---

## ServiceNow Integration

BackupPulse integrates with ServiceNow to raise and resolve incidents directly from the health report — no copy-pasting into ITSM tools.

```
Health Report UI
     │
     ├── [Raise Incident] button on each failed job row
     │         ↓
     │   POST /raise-incident
     │   • Populates: short_description, description, assignment_group
     │   • Uses TEAMS_SNOW_OFFERING for offering_id
     │   • Returns INC number instantly
     │
     └── [Resolve Incident] button
               ↓
         POST /resolve-incident
         • Accepts INC number + resolution notes
         • Sets state = Resolved in ServiceNow
         • Logs resolution to qa.jsonl
```

**What gets auto-populated in the incident:**

| Field | Value |
|---|---|
| Short description | Job name + failed object name |
| Description | Full failure message from Veeam |
| Assignment group | `SNOW_ASSIGNMENT_GROUP` from config |
| Offering ID | `TEAMS_SNOW_OFFERING` from config |

**Setup:**
1. ServiceNow developer instance — free at developer.servicenow.com
2. Add to `config_final.env`:
   ```
   SNOW_INSTANCE=https://dev123456.service-now.com
   SNOW_USER=admin
   SNOW_PASS=your-password
   SNOW_ASSIGNMENT_GROUP=Backup Operations
   TEAMS_SNOW_OFFERING=123
   ```
3. Run `python encrypt_config.py` — encrypts `SNOW_PASS` immediately
4. Restart app — Raise/Resolve buttons appear in health report

**Config keys:**

| Key | Purpose |
|---|---|
| `SNOW_INSTANCE` | Your ServiceNow instance URL |
| `SNOW_USER` | API username |
| `SNOW_PASS` | API password (auto-encrypted) |
| `SNOW_ASSIGNMENT_GROUP` | Team incidents are assigned to |
| `TEAMS_SNOW_OFFERING` | Offering/catalog ID for incident category |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web framework | Flask (Python) |
| Frontend | Vanilla HTML/CSS/JS |
| Database | PostgreSQL + psycopg2 |
| LLM — Cloud | Groq, OpenRouter, Gemini, OpenAI |
| LLM — Enterprise | AWS Bedrock (boto3) |
| LLM — Local | LM Studio |
| PCI Firewall | `pci_firewall.py` — local regex scrubber |
| Guardrails | AWS Bedrock Guardrails |
| Encryption | Fernet (cryptography lib) |
| Teams Bot | Outgoing webhook + HMAC-SHA256 |
| Ticketing | ServiceNow REST API |
| Logging | Python RotatingFileHandler |

---

## Features

- Natural language queries — no SQL knowledge needed
- Multi-server consolidation — all VBR servers in one view
- Live provider + model switching — no restart needed
- 100% local mode — LM Studio + phi-4-mini, zero internet
- Microsoft Teams bot — @mention queries from Teams channels
- PCI firewall — strips sensitive data before every LLM call
- AWS Bedrock — enterprise-grade, VPC-safe, guardrails enforced
- ServiceNow integration — raise and resolve incidents from the UI
- Engineer remarks — add action notes, AI parses them
- One-click HTML health report → email to management
- Read-only SQL — UPDATE/DELETE/DROP blocked at app and DB level
- Encrypted config — all secrets stored with Fernet encryption
- 3-tier logging — app events, errors, and Q&A in separate files
- SLA reporting — uses `last_successful_backup`, retried successes counted correctly

---

## Supported Providers

| Provider | Type | Cost | PCI Safe |
|---|---|---|---|
| **Groq** | Cloud | Free (rate limits) | PCI firewall (local) |
| **OpenRouter** | Cloud | Free models available | PCI firewall (local) |
| **AWS Bedrock** | Enterprise Cloud | Pay-per-token (~$0.30/1M) | PCI firewall + Guardrails |
| **LM Studio** | Local | Free — zero internet | Data never leaves machine |
| **Gemini / OpenAI / Copilot** | Cloud | Free tier / Paid | PCI firewall (local) |

---

## Demo Setup — Single Machine

Everything runs on one laptop. Fake data generated by `db_setup.py`.

**Demo data includes:**
- 40 protected objects: 30 VMs, 8 Agents, 2 File Servers across 3 VBR servers
- Realistic failure scenarios: CBT errors, VSS failures, agent connection issues
- NAS-SRV01 with no successful backup for 5 days (persistent failure demo)
- 30 days of job history with random failures and retries

---

## Production Setup — Secure Enterprise Deployment

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                          COMPANY INTERNAL NETWORK                            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐            ║
║  │  VBR Server 1   │   │  VBR Server 2   │   │  VBR Server 3   │            ║
║  │  Veeam B&R      │   │  Veeam B&R      │   │  Veeam B&R      │            ║
║  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘            ║
║           └─────────────────────┼─────────────────────┘                     ║
║                    Sync Agent (scheduled, every 1h)                          ║
║                                 ▼                                            ║
║  ┌──────────────────────────────────────────────────────────────────────┐   ║
║  │                        Reporting Server                               │   ║
║  │  BackupPulse Flask App                                               │   ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │   ║
║  │  │ pci_firewall │  │  PostgreSQL  │  │  LLM (LM Studio local      │ │   ║
║  │  │ (local, free)│  │  read-only   │  │  OR AWS Bedrock via VPC)   │ │   ║
║  │  └──────────────┘  └──────────────┘  └────────────────────────────┘ │   ║
║  └──────────────────────────────────────────────────────────────────────┘   ║
║                                 │                                            ║
║              Engineer / Manager Workstations + Microsoft Teams               ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## Demo vs Production

| | Demo | Production |
|---|---|---|
| Data | Fake data from `db_setup.py` | Sync agent from real VBR servers |
| LLM | Groq free / LM Studio | AWS Bedrock (VPC) or LM Studio |
| PCI protection | `pci_firewall.py` | `pci_firewall.py` + Bedrock Guardrails |
| Teams bot | ngrok tunnel | Internal URL or reverse proxy |
| Auth | None | Reverse proxy + Windows Auth |
| DB user | `veeam_bot` (read-only) | Same — read-only by design |

---

## Project Structure

```
BackupPulse/
├── app.py                    # Main Flask application
├── config_final.env          # Config (gitignored — never commit)
├── config_final.env.example  # Config template
├── encrypt_config.py         # Encrypt all secrets with Fernet
├── pci_firewall.py           # Local PCI/PII data scrubber
├── db_setup.py               # Create and seed demo database
├── start-restart.py          # Start or restart app
├── stop.py                   # Stop app
├── sql_prompt.txt            # SQL generation instructions (cloud)
├── sql_prompt_local.txt      # SQL generation instructions (local)
├── system_prompt.txt         # Answer formatting rules (cloud)
├── system_prompt_local.txt   # Answer formatting rules (local)
├── requirements.txt
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
python start-restart.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## Running 100% Locally (Air-Gapped)

1. Download LM Studio → load `phi-4-mini-instruct` (Q4_K_M)
2. Enable Local Server (port 1234)
3. Set `MODEL_PROVIDER=lmstudio` in `config_final.env`
4. Restart the app

Every question and answer stays on your machine. Zero internet.

---

## Logging

| File | Contents |
|---|---|
| `logs/app.log` | Startup, LLM timings, DB queries, provider switches |
| `logs/errors.log` | Errors and warnings only |
| `logs/qa.jsonl` | Every Q&A pair as JSON |

---

## Example Questions

```
# Chatbot or Teams (@BackupPulseBot)
Recent failed jobs
Repository capacity
SLA report for this week
Objects not backed up in last 2 days
How many servers do we have in backup?
Check last backup for LApp04
What is the restore point for WAgt03?
```

---

## What Still Needs Building for Production

| Component | Status |
|---|---|
| BackupPulse UI + AI + logging | ✅ This repo |
| PostgreSQL schema | ✅ `db_setup.py` |
| PCI firewall | ✅ `pci_firewall.py` |
| Microsoft Teams bot | ✅ Outgoing webhook |
| ServiceNow integration | ✅ Raise + resolve incidents |
| AWS Bedrock + Guardrails | ✅ boto3, VPC-ready |
| Sync agent (VBR → PostgreSQL) | ⚠️ Not included — environment-specific |
| Authentication layer | ⚠️ Not included |
| VPC endpoint setup | ⚠️ AWS infra — environment-specific |

---

## License

MIT — free to use, modify, and distribute.

*Not affiliated with Veeam Software.*
