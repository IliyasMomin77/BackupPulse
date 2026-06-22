"""
teams_queries.py — Intent templates for the Teams bot.
No SQL here — LLM generates the SQL. These provide query context
and format hints so the LLM answers consistently for each question type.
"""

# Each entry: intent (guides SQL generation) + format (guides output formatting)

SERVER_STATUS = {
    "intent": "Check if the specified server exists in protected_vms and return its backup status.",
    "format": (
        "Reply with one of:\n"
        "✅ **ServerName** is protected. Job: X | VBR: Y | Last backup: YYYY-MM-DD HH:MM\n"
        "⚠️ **ServerName** is NOT found in the backup system."
    ),
}

LAST_BACKUP = {
    "intent": (
        "Find the last successful backup for the specified server from protected_vms. "
        "Also check failed_jobs_daily for the 3 most recent failures for that server."
    ),
    "format": (
        "Show: **ServerName** — Last successful backup: YYYY-MM-DD HH:MM\n"
        "Then list recent failures (if any): ❌ YYYY-MM-DD — JobName: brief reason"
    ),
}

PROTECTED_COUNT = {
    "intent": "Count distinct protected objects from protected_vms, broken down by object_type.",
    "format": (
        "**Protected objects: N**\n"
        "- VMs: N\n"
        "- Agents: N\n"
        "- File Servers: N"
    ),
}

FAILED_JOBS = {
    "intent": (
        "Find all distinct failing objects from failed_jobs_daily in the last 24 hours. "
        "Include vbr_server, job_name, failed_object_name, failure_message. "
        "Use COUNT(DISTINCT failed_object_name) for object count."
    ),
    "format": (
        "Lead with: X job(s) had failures — Y object(s) affected in last 24h.\n"
        "Then group by VBR server:\n"
        "**VBR1**\n"
        "JobName: **ObjectName** ❌ — brief failure reason"
    ),
}

SLA_REPORT = {
    "intent": (
        "Calculate SLA for last 7 days: "
        "total distinct protected objects from protected_vms, "
        "distinct failed objects from failed_jobs_daily where backup_date >= CURRENT_DATE - 7, "
        "and SLA percentage = (total - failed) / total * 100."
    ),
    "format": (
        "**SLA Report — Last 7 Days**\n"
        "SLA: N% (✅ / ⚠️ / 🔴)\n"
        "- Protected: N | Successful: N | Failed: N"
    ),
}

LIST_VMS = {
    "intent": "List protected objects from protected_vms matching the given name pattern, including object_type and job_name.",
    "format": (
        "**Protected objects matching '<pattern>' (N found):**\n"
        "• **ObjectName** (Type) — JobName"
    ),
}

GUIDANCE = {
    "intent": None,  # No DB query needed
    "format": None,
}
