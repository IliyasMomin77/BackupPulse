"""
Health report SQL queries.
All thresholds and SQL live here — app.py imports and runs them, nothing hardcoded there.
"""

FAILED_JOBS_24H = """
    SELECT MAX(backup_date) AS backup_date, vbr_server, job_name,
           failed_object_name, MAX(failure_message) AS failure_message
    FROM failed_jobs_daily
    WHERE backup_date >= CURRENT_DATE - INTERVAL '1 day'
    GROUP BY vbr_server, job_name, failed_object_name
    ORDER BY vbr_server, job_name, failed_object_name
"""

CRITICAL_REPOS = """
    SELECT repo_name, sobr_name, repo_type, vbr_server,
           total_tb, used_tb, free_tb, used_pct
    FROM repositories
    WHERE used_pct >= 90
    ORDER BY used_pct DESC
"""

# Warning: > 6 hours  |  Critical: >= 24 hours
LONG_RUNNING_JOBS = """
    SELECT job_name, backup_type, vbr_server, start_time,
           ROUND(duration_minutes / 60.0, 1) AS duration_hours,
           CASE WHEN duration_minutes >= 1440 THEN 'Critical'
                ELSE 'Warning'
           END AS alert_level
    FROM job_sessions
    WHERE duration_minutes > 360
      AND start_time >= NOW() - INTERVAL '1 day'
    ORDER BY duration_minutes DESC
    LIMIT 10
"""

ZERO_SIZE_JOBS = """
    SELECT backup_date, job_name, vbr_server, vm_name,
           read_mb, transferred_kb, vm_start_time
    FROM zero_size_jobs
    WHERE backup_date >= CURRENT_DATE - 1
    ORDER BY vm_start_time DESC
    LIMIT 20
"""
