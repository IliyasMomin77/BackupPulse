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

script   = os.path.join(os.path.dirname(__file__), 'app.py')
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)
log      = open(os.path.join(logs_dir, 'app.log'), 'a')
subprocess.Popen(
    [sys.executable, script],
    stdout=log,
    stderr=log,
    stdin=subprocess.DEVNULL,
    creationflags=subprocess.DETACHED_PROCESS
)
time.sleep(3)
print(f"App started: http://127.0.0.1:{PORT}")
print(f"Logs: logs/app.log")
