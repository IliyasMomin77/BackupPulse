import subprocess, sys, time, os

PORT = 5000


def find_pid():
    result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and f':{PORT}' in parts[1] and parts[3] == 'LISTENING':
            return parts[4]
    return None


pid = find_pid()
if pid:
    print(f"App running on port {PORT} (PID {pid}) — stopping...")
    kill = subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, text=True)
    if kill.returncode != 0:
        print(f"Failed to stop: {kill.stderr.strip()}")
        print("Try running as Administrator.")
        sys.exit(1)
    print("Stopped. Starting fresh...")
    time.sleep(1)
else:
    print("App not running. Starting...")

base     = os.path.dirname(os.path.abspath(__file__))
# pythonw.exe = windowless Python — no console window, stdout/stderr still captured to log file
venv_pyw = os.path.join(base, 'venv', 'Scripts', 'pythonw.exe')
venv_py  = os.path.join(base, 'venv', 'Scripts', 'python.exe')
python   = venv_pyw if os.path.exists(venv_pyw) else (venv_py if os.path.exists(venv_py) else sys.executable)
script   = os.path.join(base, 'app.py')
logs_dir = os.path.join(base, 'logs')
os.makedirs(logs_dir, exist_ok=True)
log      = open(os.path.join(logs_dir, 'app.log'), 'a')
subprocess.Popen(
    [python, script],
    stdout=log,
    stderr=log,
    stdin=subprocess.DEVNULL,
    creationflags=subprocess.DETACHED_PROCESS
)
time.sleep(3)
print(f"App started: http://127.0.0.1:{PORT}")
print(f"Logs: logs/app.log")
