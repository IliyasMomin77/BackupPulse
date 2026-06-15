"""
db_setup.py — Seed demo data
40 protected objects: 30 VM, 8 Agent, 2 File Backup
VBR servers: VBR1, VBR2, VBR3
Failures:
  Prod_VM_Job   (VBR1): WApp03, WApp04, WApp05  — same CBT error
  Linux_VM_Job  (VBR2): LApp02, LApp03           — same VSS error
  Prod_Agent_Job(VBR1): WAgt03, WAgt04            — same agent conn error
  Prod_File_Job (VBR3): NAS-SRV01                — access denied
"""

import psycopg2
import os
from datetime import date, datetime, timedelta
import random

random.seed(77)


def _load_config():
    base     = os.path.dirname(__file__)
    cfg_path = os.path.join(base, 'config_final.env')
    key_path = os.path.join(base, 'secret.key')
    fernet   = None
    if os.path.exists(key_path):
        from cryptography.fernet import Fernet
        fernet = Fernet(open(key_path, 'rb').read())
    with open(cfg_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if fernet and v.startswith('ENC:'):
                v = fernet.decrypt(v[4:].encode()).decode()
            os.environ[k] = v

_load_config()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "veeam_demo"),
    "user":     os.getenv("DB_SETUP_USER", "postgres"),
    "password": os.getenv("DB_SETUP_PASSWORD", ""),
    "port":     5432,
}

VBR_SERVERS = ["VBR1", "VBR2", "VBR3"]

# ── Protected objects ──────────────────────────────────────────────────────────

VMS = [
    # Windows App VMs (10)
    "WApp01","WApp02","WApp03","WApp04","WApp05",
    "WApp06","WApp07","WApp08","WApp09","WApp10",
    # Linux App VMs (5)
    "LApp01","LApp02","LApp03","LApp04","LApp05",
    # Windows Web VMs (5)
    "WWeb01","WWeb02","WWeb03","WWeb04","WWeb05",
    # Linux Web VMs (3)
    "LWeb01","LWeb02","LWeb03",
    # SQL Server VMs (5)
    "WSQL01","WSQL02","WSQL03","WSQL04","WSQL05",
    # Windows DB VMs (2)
    "WDB01","WDB02",
]  # 30 total

AGENTS = [
    # Windows physical agents (5)
    "WAgt01","WAgt02","WAgt03","WAgt04","WAgt05",
    # Linux physical agents (3)
    "LAgt01","LAgt02","LAgt03",
]  # 8 total

FILES = ["NAS-SRV01", "NFS-SRV01"]  # 2 total

ALL_OBJECTS = VMS + AGENTS + FILES   # 40 total

# Per-job failures: multiple objects share the same root-cause error
JOB_FAILURES = {
    "Prod_VM_Job": {
        "objects": ["WApp03", "WApp04", "WApp05"],
        "error":   "CBT data is invalid, failing over to legacy incremental backup. Please reset CBT on the VM.",
    },
    "Linux_VM_Job": {
        "objects": ["LApp02", "LApp03"],
        "error":   "VSS error: VSS_E_SNAPSHOT_SET_IN_PROGRESS. Code: 0x80042316. Another snapshot operation is in progress.",
    },
    "Prod_Agent_Job": {
        "objects": ["WAgt03", "WAgt04"],
        "error":   "Failed to establish connection: the Veeam Agent service is unavailable on the target machine.",
    },
    "Prod_File_Job": {
        "objects": ["NAS-SRV01"],
        "error":   "Access is denied. Your organization's security policies block unauthenticated guest access to this shared folder.",
    },
}

# Flat lists derived from JOB_FAILURES
BAD_OBJECTS   = [o for f in JOB_FAILURES.values() for o in f["objects"]]
BAD_OBJ_ERROR = {o: f["error"] for f in JOB_FAILURES.values() for o in f["objects"]}
JOB_ERR_MSG   = {jname: f["error"] for jname, f in JOB_FAILURES.items()}

# ── Job definitions ────────────────────────────────────────────────────────────
WIN_VMS   = [v for v in VMS if v.startswith(("WApp","WWeb","WSQL","WDB"))]
LNX_VMS   = [v for v in VMS if v.startswith(("LApp","LWeb"))]
WIN_AGTS  = [a for a in AGENTS if a.startswith("WAgt")]
LNX_AGTS  = [a for a in AGENTS if a.startswith("LAgt")]

# (job_name, objects, backup_type, vbr_server, avg_duration_min, avg_size_gb)
JOB_DEFS = [
    ("Prod_VM_Job",      WIN_VMS,  "VM Backup",   "VBR1", 55, 12.0),
    ("Linux_VM_Job",     LNX_VMS,  "VM Backup",   "VBR2", 40,  6.0),
    ("Prod_Agent_Job",   WIN_AGTS, "Agent",        "VBR1", 40,  6.0),
    ("Linux_Agent_Job",  LNX_AGTS, "Agent",        "VBR2", 45,  5.0),
    ("Prod_File_Job",    FILES,    "File Backup",  "VBR3", 70, 30.0),
]

OBJ_TO_JOB = {}
for (jname, objs, btype, vbr, dur, sz) in JOB_DEFS:
    for o in objs:
        OBJ_TO_JOB[o] = (jname, btype, vbr)

# ── Failure messages ───────────────────────────────────────────────────────────
VM_FAIL_MSGS = [
    "Failed to create VSS snapshot: provider error 0x800423f4. A VSS critical writer has failed.",
    "VSS error: VSS_E_SNAPSHOT_SET_IN_PROGRESS. Code: 0x80042316. Another snapshot operation is in progress.",
    "CBT data is invalid, failing over to legacy incremental backup. Please reset CBT on the VM.",
    "CreateSnapshot failed: timeout 1800000 ms exceeded. Storage may be under heavy load.",
    "NFC storage connection is unavailable. Check network path between proxy and ESXi host.",
    "SetVmChangeTracking failed: NoPermissionFault. Check vCenter service account permissions.",
    "Failed to create NFC download stream. Verify ESXi host is reachable on port 902.",
    "VM is unavailable and will be skipped from processing. Host may be in maintenance mode.",
    "The object has been deleted or is no longer accessible in vCenter inventory.",
    "Failed to finalize guest processing. VMware Tools may not be running or responding.",
]
AGENT_FAIL_MSGS = [
    "Failed to connect to the guest agent. Cannot connect to admin share. Win32 error: The network name cannot be found. Code: 67.",
    "VSS_WS_FAILED_AT_POST_SNAPSHOT. Application-consistent backup could not be completed.",
    "There is not enough space on the disk. Failed to write data to the backup file.",
    "Failed to establish connection: the Veeam Agent service is unavailable on the target machine.",
    "Failed to prepare guest for SQL Server transaction log backup: target machine actively refused connection.",
    "Unable to perform application-aware processing: cannot establish connection to the guest OS.",
    "I/O device error: failed to write data to backup file. Check disk health on agent host.",
    "Failed to compact full backup file. Agent: Failed to process method {BackupAgent.Compact}.",
]
FILE_FAIL_MSGS = [
    "Access is denied. Your organization's security policies block unauthenticated guest access to this shared folder.",
    "Failed to process NAS backup task: Error: Agent: Failed to process method {NasMaster.SaveSourceBackupMeta}.",
    "Item is locked by running session [File Backup]. Retry after current session completes.",
    "SMB 3.0 required. Share is not accessible using the current SMB protocol version.",
    "Container does not exist or is no longer accessible. Verify the NAS share path and credentials.",
    "Backing up 0 files and 0 folders (0 B transferred). File share may be empty or path is incorrect.",
    "Failed to write data to the file. NAS destination repository may have insufficient space.",
]

def fail_msg_for(obj):
    if obj in AGENTS:  return random.choice(AGENT_FAIL_MSGS)
    if obj in FILES:   return random.choice(FILE_FAIL_MSGS)
    return random.choice(VM_FAIL_MSGS)

# ── Repositories (all below 90%) ───────────────────────────────────────────────
REPO_DEFS = [
    ("PROD-REPO-01", None, "Repository", 20480, 14336),  # 70% — VBR1
    ("PROD-REPO-02", None, "Repository", 30720, 22528),  # 73% — VBR2
    ("DR-REPO-01",   None, "Repository", 20480, 10240),  # 50% — VBR3
    ("OFFSITE-REPO", None, "Repository", 15360, 12288),  # 80% — VBR1
    ("TAPE-ARCHIVE", None, "Repository", 102400,61440),  # 60% — VBR2
]
REPO_VBR = ["VBR1","VBR2","VBR3","VBR1","VBR2"]

# ── Helpers ────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def rnd_dt(base_date, hour_min=0, hour_max=23):
    return datetime.combine(base_date, datetime.min.time()).replace(
        hour=random.randint(hour_min, hour_max),
        minute=random.randint(0, 59),
        second=random.randint(0, 59)
    )

# ── DDL ───────────────────────────────────────────────────────────────────────
CREATE_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS failed_jobs_daily (
        id                  SERIAL PRIMARY KEY,
        backup_date         DATE,
        vbr_server          VARCHAR(50),
        job_name            VARCHAR(255),
        failed_object_name  VARCHAR(255),
        failure_message     TEXT,
        pulled_at           TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS job_sessions (
        id               SERIAL PRIMARY KEY,
        job_name         VARCHAR(255),
        backup_type      VARCHAR(50),
        run_type         VARCHAR(20),
        vbr_server       VARCHAR(50),
        start_time       TIMESTAMP,
        end_time         TIMESTAMP,
        duration_minutes DECIMAL(10,2),
        status           VARCHAR(10),
        failure_message  TEXT,
        size_gb          DECIMAL(10,2),
        pulled_at        TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS protected_vms (
        id                     SERIAL PRIMARY KEY,
        object_name            VARCHAR(255),
        object_type            VARCHAR(20),
        job_name               VARCHAR(255),
        vbr_server             VARCHAR(50),
        last_successful_backup TIMESTAMP,
        active_restore_points  INTEGER,
        size_gb                DECIMAL(10,2),
        pulled_at              TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS repositories (
        id          SERIAL PRIMARY KEY,
        repo_type   VARCHAR(50),
        sobr_name   VARCHAR(255),
        repo_name   VARCHAR(255),
        vbr_server  VARCHAR(50),
        total_tb    DECIMAL(10,2),
        used_tb     DECIMAL(10,2),
        free_tb     DECIMAL(10,2),
        used_pct    DECIMAL(5,2),
        imported_at TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS zero_size_jobs (
        id             SERIAL PRIMARY KEY,
        backup_date    DATE,
        job_name       VARCHAR(255),
        vbr_server     VARCHAR(50),
        vm_name        VARCHAR(255),
        read_mb        DECIMAL(10,2),
        transferred_kb DECIMAL(10,2),
        vm_start_time  TIMESTAMP,
        imported_at    TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS long_running_jobs (
        id             SERIAL PRIMARY KEY,
        job_name       VARCHAR(255),
        backup_type    VARCHAR(50),
        vbr_server     VARCHAR(50),
        start_time     TIMESTAMP,
        duration_hours DECIMAL(5,2),
        alert_level    VARCHAR(20),
        imported_at    TIMESTAMP DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS restore_points (
        id                 SERIAL PRIMARY KEY,
        object_name        VARCHAR(255),
        vbr_server         VARCHAR(50),
        backup_type        VARCHAR(20),
        restore_point_date TIMESTAMP,
        size_gb            DECIMAL(10,2),
        pulled_at          TIMESTAMP DEFAULT NOW()
    )""",
]

TABLES = [
    "failed_jobs_daily","job_sessions","protected_vms",
    "repositories","zero_size_jobs","long_running_jobs","restore_points",
]

def create_tables(cur):
    for stmt in CREATE_STATEMENTS:
        cur.execute(stmt)
    # Add new columns to existing tables without dropping data
    cur.execute("""
        ALTER TABLE failed_jobs_daily
        ADD COLUMN IF NOT EXISTS failure_message TEXT
    """)

def truncate_tables(cur):
    for t in TABLES:
        cur.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")


# ── Seed functions ─────────────────────────────────────────────────────────────

def insert_repositories(cur):
    for i, (rname, sobr, rtype, total_gb, used_gb) in enumerate(REPO_DEFS):
        free_gb = total_gb - used_gb
        pct = round((used_gb / total_gb) * 100, 2)
        cur.execute(
            """INSERT INTO repositories
               (repo_type, sobr_name, repo_name, vbr_server,
                total_tb, used_tb, free_tb, used_pct)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rtype, sobr, rname, REPO_VBR[i],
             round(total_gb/1024,2), round(used_gb/1024,2),
             round(free_gb/1024,2), pct),
        )
    print(f"  Repositories: {len(REPO_DEFS)} rows (all below 90%)")


def insert_protected_vms(cur):
    now = datetime.now()
    for obj in ALL_OBJECTS:
        jname, btype, vbr = OBJ_TO_JOB.get(obj, ("Prod_VM_Job","VM Backup","VBR1"))
        if obj in FILES:
            otype, sz = "File Server", round(random.uniform(200, 800), 2)
        elif obj in AGENTS:
            otype, sz = "Agent", round(random.uniform(50, 200), 2)
        else:
            otype, sz = "VM", round(random.uniform(40, 400), 2)

        if obj in BAD_OBJECTS:
            last_backup = now - timedelta(hours=random.randint(26, 48))
            rp_count = random.randint(3, 8)
        else:
            last_backup = now - timedelta(hours=random.randint(1, 20))
            rp_count = random.randint(14, 30)

        cur.execute(
            """INSERT INTO protected_vms
               (object_name, object_type, job_name, vbr_server,
                last_successful_backup, active_restore_points, size_gb)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (obj, otype, jname, vbr, last_backup, rp_count, sz),
        )
    print(f"  Protected objects: {len(ALL_OBJECTS)} rows (30 VM, 8 Agent, 2 File)")


def insert_job_sessions(cur):
    today = date.today()
    count = 0
    for day_offset in range(30, -1, -1):
        run_date = today - timedelta(days=day_offset)
        for (jname, objs, btype, vbr, avg_dur, avg_size) in JOB_DEFS:
            hour_min, hour_max = (1, 6) if day_offset == 0 else (20, 23)
            start = rnd_dt(run_date, hour_min, hour_max)
            dur = max(5, random.gauss(avg_dur, avg_dur * 0.15))
            has_bad = any(o in BAD_OBJECTS for o in objs)
            if has_bad and random.random() < 0.6:
                status   = random.choice(["Failed", "Warning"])
                fail_msg = JOB_ERR_MSG.get(jname, fail_msg_for(next(o for o in objs if o in BAD_OBJECTS)))
                dur     *= random.uniform(0.4, 0.8)
            elif random.random() < 0.04:
                status, fail_msg = "Warning", "Job completed with warnings."
            else:
                status, fail_msg = "Success", None
            end  = start + timedelta(minutes=dur)
            size = round(max(1.0, random.gauss(avg_size*len(objs), avg_size*len(objs)*0.1)), 2)
            cur.execute(
                """INSERT INTO job_sessions
                   (job_name, backup_type, run_type, vbr_server,
                    start_time, end_time, duration_minutes,
                    status, failure_message, size_gb)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (jname, btype, "Incremental", vbr, start, end,
                 round(dur,2), status, fail_msg, size),
            )
            count += 1
    print(f"  Job sessions: {count} rows")


def insert_failed_jobs_daily(cur):
    today = date.today()
    count = 0
    for day_offset in range(30, -1, -1):
        run_date = today - timedelta(days=day_offset)
        # Last 3 days: all 4 bad objects ALWAYS fail (guaranteed for demo queries)
        # Older history: 65% chance so it looks realistic
        guaranteed = day_offset <= 2
        for obj in BAD_OBJECTS:
            if guaranteed or random.random() < 0.65:
                jname, _, vbr = OBJ_TO_JOB[obj]
                cur.execute(
                    """INSERT INTO failed_jobs_daily
                       (backup_date, vbr_server, job_name, failed_object_name, failure_message)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (run_date, vbr, jname, obj, BAD_OBJ_ERROR[obj]),
                )
                count += 1
        # Occasional random failure from a healthy object
        if random.random() < 0.3:
            obj = random.choice([o for o in ALL_OBJECTS if o not in BAD_OBJECTS])
            jname, _, vbr = OBJ_TO_JOB[obj]
            cur.execute(
                """INSERT INTO failed_jobs_daily
                   (backup_date, vbr_server, job_name, failed_object_name, failure_message)
                   VALUES (%s,%s,%s,%s,%s)""",
                (run_date, vbr, jname, obj, fail_msg_for(obj)),
            )
            count += 1
    print(f"  Failed jobs daily: {count} rows")


def insert_zero_size_jobs(cur):
    today = date.today()
    count = 0
    candidates = random.sample(ALL_OBJECTS, 4)
    for day_offset in range(7, -1, -1):
        run_date = today - timedelta(days=day_offset)
        if random.random() < 0.4:
            obj = random.choice(candidates)
            jname, _, vbr = OBJ_TO_JOB[obj]
            cur.execute(
                """INSERT INTO zero_size_jobs
                   (backup_date, job_name, vbr_server, vm_name,
                    read_mb, transferred_kb, vm_start_time)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (run_date, jname, vbr, obj, 0, 0, rnd_dt(run_date, 20, 23)),
            )
            count += 1
    print(f"  Zero-size jobs: {count} rows")


def insert_long_running_jobs(cur):
    now = datetime.now()
    records = [
        ("Prod_VM_Job",   "VM Backup",   "VBR1", 3, 8.5, "Warning"),
        ("Prod_File_Job", "File Backup", "VBR3", 5, 9.2, "Warning"),
    ]
    for (jname, btype, vbr, hrs_ago, dur_h, alert) in records:
        cur.execute(
            """INSERT INTO long_running_jobs
               (job_name, backup_type, vbr_server, start_time,
                duration_hours, alert_level)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (jname, btype, vbr, now - timedelta(hours=hrs_ago), dur_h, alert),
        )
    print(f"  Long-running jobs: {len(records)} rows")


def insert_restore_points(cur):
    now = datetime.now()
    count = 0
    for obj in ALL_OBJECTS:
        _, btype, vbr = OBJ_TO_JOB[obj]
        num_points = random.randint(3, 8) if obj in BAD_OBJECTS else random.randint(14, 30)
        base_size  = random.uniform(5, 40)
        for i in range(num_points):
            rp_date = now - timedelta(days=i, hours=random.randint(0, 3))
            rp_type = "Full" if i % 7 == 0 else "Incremental"
            rp_size = round(base_size if rp_type == "Full" else base_size * random.uniform(0.02, 0.12), 2)
            cur.execute(
                """INSERT INTO restore_points
                   (object_name, vbr_server, backup_type,
                    restore_point_date, size_gb)
                   VALUES (%s,%s,%s,%s,%s)""",
                (obj, vbr, rp_type, rp_date, rp_size),
            )
            count += 1
    print(f"  Restore points: {count} rows")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=== Veeam Demo DB Setup ===")
    print(f"VBR Servers : VBR1, VBR2, VBR3")
    print(f"Jobs        : Prod_VM_Job (VBR1), Linux_VM_Job (VBR2),")
    print(f"              Prod_Agent_Job (VBR1), Linux_Agent_Job (VBR2), Prod_File_Job (VBR3)")
    print(f"Objects     : 30 VM + 8 Agent + 2 File = 40 total")
    print(f"Bad objects : {len(BAD_OBJECTS)} total — {BAD_OBJECTS}")
    for jname, f in JOB_FAILURES.items():
        print(f"  {jname}: {f['objects']} — same error")

    conn = get_conn()
    cur  = conn.cursor()
    create_tables(cur);  conn.commit()
    truncate_tables(cur); conn.commit()

    print("\nInserting data:")
    insert_repositories(cur)
    insert_protected_vms(cur)
    insert_job_sessions(cur)
    insert_failed_jobs_daily(cur)
    insert_zero_size_jobs(cur)
    insert_long_running_jobs(cur)
    insert_restore_points(cur)
    conn.commit()
    cur.close(); conn.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
