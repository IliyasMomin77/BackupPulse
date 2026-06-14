import subprocess, sys

PORT = 5000

result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
pid = None
for line in result.stdout.splitlines():
    parts = line.split()
    if len(parts) >= 5 and f':{PORT}' in parts[1] and parts[3] == 'LISTENING':
        pid = parts[4]
        break

if not pid:
    print("App is not running")
    sys.exit(0)

print(f"Found app on port {PORT} (PID {pid}) — stopping...")
kill = subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, text=True)
if kill.returncode == 0:
    print("App stopped.")
else:
    print(f"Failed to stop: {kill.stderr.strip()}")
    print("Try running as Administrator")
