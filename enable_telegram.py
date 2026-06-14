"""
enable_telegram.py  —  Connect or disconnect the Telegram bot.

Usage:
  python enable_telegram.py          # interactive (prompts for token + ngrok URL)
  python enable_telegram.py --off    # blank the token (disconnect)
"""

import sys
import re
import requests

CONFIG = "config_final.env"


def read_config():
    with open(CONFIG, encoding="utf-8") as f:
        return f.read()


def write_token(content, token):
    return re.sub(r"(?m)^TELEGRAM_TOKEN=.*$", f"TELEGRAM_TOKEN={token}", content)


def register_webhook(token, ngrok_url):
    url = ngrok_url.rstrip("/") + "/telegram"
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/setWebhook",
        params={"url": url},
        timeout=10,
    )
    return resp.json()


def detect_ngrok():
    """Try to auto-detect ngrok public URL from its local API."""
    try:
        data = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=3).json()
        for t in data.get("tunnels", []):
            if t.get("proto") == "https":
                return t["public_url"]
    except Exception:
        pass
    return None


def main():
    # ── Disconnect mode ──────────────────────────────────────────
    if "--off" in sys.argv:
        content = write_token(read_config(), "")
        with open(CONFIG, "w", encoding="utf-8") as f:
            f.write(content)
        print("Telegram disconnected. Restart BackupPulse to apply.")
        return

    # ── Connect mode ─────────────────────────────────────────────
    print("=== Enable Telegram Bot ===\n")

    token = input("Paste your Telegram bot token: ").strip()
    if not token:
        print("No token entered. Aborted.")
        return

    # Try auto-detect ngrok, otherwise ask
    ngrok_url = detect_ngrok()
    if ngrok_url:
        print(f"Detected ngrok URL: {ngrok_url}")
        confirm = input("Use this? [Y/n]: ").strip().lower()
        if confirm == "n":
            ngrok_url = None

    if not ngrok_url:
        ngrok_url = input("Enter your ngrok HTTPS URL (e.g. https://abc123.ngrok-free.app): ").strip()

    if not ngrok_url.startswith("https://"):
        print("URL must start with https://. Aborted.")
        return

    # Register webhook
    print("\nRegistering webhook with Telegram...")
    result = register_webhook(token, ngrok_url)
    if not result.get("ok"):
        print(f"Webhook registration failed: {result}")
        return

    print(f"Webhook set: {ngrok_url}/telegram")

    # Save token to config
    content = write_token(read_config(), token)
    with open(CONFIG, "w", encoding="utf-8") as f:
        f.write(content)

    print("\nTelegram token saved to config_final.env")
    print("Restart BackupPulse to activate the bot.")
    print("\nDone! Open Telegram and message your bot to test.")


if __name__ == "__main__":
    main()
