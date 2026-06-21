"""
local_query.py — SQL cleanup and result formatting for local models (LM Studio).

Local small models (phi-4-mini, 3.8B) cannot reliably handle complex prompts
or explanation tasks, so this module:
  1. Extracts and sanitises the SELECT query the local LLM returns.
  2. Injects missing date filters as a safety net.
  3. Formats DB rows as readable plain text (no second LLM call needed).
"""

import re
import logging
from collections import defaultdict, Counter

log = logging.getLogger(__name__)

# Keywords that indicate the user is asking about failed/recent jobs
FAILED_KW = re.compile(
    r'\b(fail|failed|failing|error|recent|job|jobs|backup|yesterday|today|last\s*24)\b',
    re.IGNORECASE
)


def extract_sql(raw: str) -> str:
    """Strip markdown fences and extract the first SELECT statement."""
    sql = re.sub(r"```sql|```", "", raw or "").strip()
    m = re.search(r"(SELECT\b[^;]+;?)", sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def inject_date_filter(sql: str) -> str:
    """
    Safety net for local models: if querying failed_jobs_daily without a
    date filter the result set is huge. Inject last-24h filter when missing.
    """
    if not re.search(r'failed_jobs_daily', sql, re.IGNORECASE):
        return sql
    if re.search(r'backup_date\s*[><=!]', sql, re.IGNORECASE):
        return sql  # already filtered

    if re.search(r'\bWHERE\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bWHERE\b', 'WHERE backup_date >= CURRENT_DATE - 1 AND ',
                     sql, count=1, flags=re.IGNORECASE)
    elif re.search(r'\bORDER BY\b', sql, re.IGNORECASE):
        sql = re.sub(r'\bORDER BY\b', 'WHERE backup_date >= CURRENT_DATE - 1 ORDER BY ',
                     sql, count=1, flags=re.IGNORECASE)
    else:
        sql = sql.rstrip(';') + ' WHERE backup_date >= CURRENT_DATE - 1'

    log.info("[LOCAL] injected missing date filter into query")
    return sql


def format_rows(question: str, rows: list) -> str:
    """Format DB rows as plain text. No LLM call — deterministic output."""
    if not rows:
        return "No records found."
    keys = list(rows[0].keys())

    # Failed jobs — group by vbr_server → job_name
    if "failed_object_name" in keys:
        grouped = defaultdict(lambda: defaultdict(list))
        for r in rows[:60]:
            srv   = r.get("vbr_server", "?")
            job   = r.get("job_name", "?")
            obj   = r.get("failed_object_name", "?")
            otype = r.get("object_type", "")
            msg   = r.get("failure_message", "")
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
        return "\n".join(lines)

    # Long-running jobs
    if "duration_hours" in keys:
        lines = ["**Long-running jobs:**"]
        for r in rows:
            alert = r.get("alert_level", "")
            flag  = " \U0001f534" if alert == "Critical" else " ⚠️"
            lines.append(f"  • **{r.get('job_name')}** on {r.get('vbr_server')} — "
                         f"{r.get('duration_hours')}h{flag}")
        return "\n".join(lines)

    # Job sessions summary
    if "status" in keys and "duration_minutes" in keys:
        counts = Counter(r.get("status") for r in rows)
        lines = [
            f"**{len(rows)} job session(s):**",
            f"  ✅ Success: {counts.get('Success', 0)}  "
            f"⚠️ Warning: {counts.get('Warning', 0)}  "
            f"❌ Failed: {counts.get('Failed', 0)}"
        ]
        for r in [x for x in rows if x.get("status") == "Failed"][:5]:
            lines.append(f"  • **{r.get('job_name')}** — {r.get('failure_message', '')}")
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

    # Protected objects list
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
            lines.append(f"  • {r.get('restore_point_date')}  –  "
                         f"{r.get('backup_type')}  ({r.get('size_gb')} GB)")
        return "\n".join(lines)

    # Repositories
    if "used_pct" in keys:
        lines = ["**Repository capacity:**"]
        for r in rows:
            pct  = float(r.get("used_pct", 0))
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
