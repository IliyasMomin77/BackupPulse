# Google Chat Bot Setup — BackupPulse

## What you need
- Google account
- BackupPulse running on your laptop (port 5000)
- ngrok account (free) — ngrok.com

---

## Step 1 — Install and start ngrok

1. Sign up free at https://ngrok.com
2. Download ngrok and unzip it anywhere
3. Run in a terminal (keep this open the whole demo):
   ```
   ngrok http 5000
   ```
4. Copy the HTTPS URL shown, e.g.:
   ```
   https://abc123.ngrok-free.app
   ```
   > This URL changes every time you restart ngrok (free plan). Update Code.gs if it changes.

---

## Step 2 — Create the Apps Script project

1. Go to https://script.google.com
2. Click **New project**
3. Name it: `BackupPulse Bot`
4. Delete all default code
5. Paste the contents of `Code.gs` from this folder
6. Change line 4 to your ngrok URL:
   ```javascript
   var BACKUPPULSE_URL = 'https://abc123.ngrok-free.app/chat';
   ```
7. Press **Ctrl+S** to save
8. Click the gear icon (⚙️ Project Settings) — copy the **Script ID** shown there

---

## Step 3 — Create Google Cloud project and enable Chat API

1. Go to https://console.cloud.google.com
2. Top bar → click the project dropdown → **New Project**
   - Name: `BackupPulse`
   - Click **Create**
3. Make sure the new project is selected in the top bar
4. Go to **APIs & Services → Library**
5. Search `Google Chat API` → click it → click **Enable**

---

## Step 4 — Configure the Chat App

1. Go to **APIs & Services → Google Chat API → Configuration**
2. Fill in:
   - **App name:** BackupPulse
   - **Description:** Veeam Backup AI Assistant
   - **Avatar URL:** leave blank (or use any image URL)
3. Under **Functionality:**
   - Check: Receive 1:1 messages
   - Check: Join spaces and group conversations
4. Under **Connection settings:**
   - Select: **Apps Script**
   - Paste your **Script ID** from Step 2
5. Under **Visibility:**
   - Add your Google account email (for testing)
6. Click **Save**

---

## Step 5 — Test it

1. Open Google Chat at https://chat.google.com
2. Click **+** next to **Direct messages**
3. Search for `BackupPulse` and start a chat
4. Type: `recent failed jobs`
5. You should get the answer from BackupPulse

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Bot not found in Chat | Wait 1-2 min after saving configuration, then refresh |
| "BackupPulse is not reachable" | Check ngrok is running and the URL in Code.gs matches |
| "Error reaching BackupPulse" | Make sure `python start.py` is running on port 5000 |
| ngrok URL expired | Free ngrok URLs expire when the terminal closes — restart ngrok and update Code.gs |

---

## Notes

- The ngrok tunnel must be running for the bot to work — keep that terminal open during demos
- Every message you send the bot goes: Google Chat → Apps Script → ngrok → BackupPulse → back
- The LLM (phi-4-mini or Groq) is called by BackupPulse, not by this script
- All your data stays on your laptop — this script only passes the question text and receives the answer
