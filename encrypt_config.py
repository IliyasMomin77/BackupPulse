"""
Run once to encrypt sensitive values in config_final.env.
Re-running is safe — already-encrypted values are skipped.
The decryption key is saved to secret.key (keep this file private).
"""
from cryptography.fernet import Fernet
import os

KEY_FILE    = os.path.join(os.path.dirname(__file__), 'secret.key')
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config_final.env')

SENSITIVE = {
    'GROQ_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY',
    'COPILOT_API_KEY', 'OPENROUTER_API_KEY', 'CLAUDE_API_KEY',
    'DB_PASSWORD', 'TEAMS_WEBHOOK_SECRET','DB_SETUP_PASSWORD', "SNOW_PASS", "SMTP_PASS",
}

# Generate key if not present
if os.path.exists(KEY_FILE):
    key = open(KEY_FILE, 'rb').read()
    print(f"Using existing key: {KEY_FILE}")
else:
    key = Fernet.generate_key()
    open(KEY_FILE, 'wb').write(key)
    print(f"Generated new key:  {KEY_FILE}")

fernet = Fernet(key)

lines = open(CONFIG_FILE, encoding='utf-8').readlines()
new_lines = []
for line in lines:
    stripped = line.strip()
    if stripped and not stripped.startswith('#') and '=' in stripped:
        k, _, v = stripped.partition('=')
        k, v = k.strip(), v.strip()
        if k in SENSITIVE and v and not v.startswith('ENC:'):
            encrypted = fernet.encrypt(v.encode()).decode()
            new_lines.append(f"{k}=ENC:{encrypted}\n")
            print(f"  Encrypted: {k}")
            continue
    new_lines.append(line)

open(CONFIG_FILE, 'w', encoding='utf-8').write(''.join(new_lines))
print("\nDone. config_final.env updated — sensitive values are now encrypted.")
print("Keep secret.key safe and out of source control.")
