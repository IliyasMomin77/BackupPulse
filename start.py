import subprocess, sys, time, os

PORT = 5000

# Check if already running
result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
for line in result.stdout.splitlines():
    parts = line.split()
    if len(parts) >= 5 and f':{PORT}' in parts[1] and parts[3] == 'LISTENING':
        print(f"App already running on port {PORT}. Run stop.py first.")
        sys.exit(1)

# Start in background — no window, logs go to logs/app.log
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
