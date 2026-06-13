# Microsoft Teams Bot Setup — BackupPulse

## What you need
- Microsoft account with Teams access (free Teams works)
- Azure account (free) — portal.azure.com
- Node.js installed
- ngrok running (same as Google Chat setup)

---

## Step 1 — Start ngrok (same as Google Chat)

```
ngrok http 3978
```
Copy the HTTPS URL, e.g. `https://xyz789.ngrok-free.app`

---

## Step 2 — Register a bot in Azure

1. Go to https://portal.azure.com
2. Search for **Azure Bot** → Create
   - Bot handle: `BackupPulse`
   - Subscription: your subscription
   - Resource group: create new → `BackupPulse-RG`
   - Pricing tier: **F0 (Free)** — 10,000 messages/month
   - Microsoft App ID: select **Create new Microsoft App ID**
3. Click **Create** — wait ~2 minutes
4. After creation, go to the resource → **Configuration**
   - Set **Messaging endpoint:** `https://xyz789.ngrok-free.app/api/messages`
   - Save
5. Go to **Configuration → Manage** (next to Microsoft App ID)
   - Click **New client secret** → Add → copy the secret value immediately

---

## Step 3 — Note your credentials

You need two values from Step 2:
- **Microsoft App ID** (shown in Azure Bot → Configuration)
- **Client Secret** (copied in Step 2, step 5)

---

## Step 4 — Run the Teams bot

```bash
cd integrations/teams
npm install

# Set credentials (Windows)
set MicrosoftAppId=YOUR_APP_ID
set MicrosoftAppPassword=YOUR_CLIENT_SECRET
set BACKUPPULSE_URL=http://localhost:5000/chat

node index.js
```

Bot will start on port 3978.

---

## Step 5 — Add the bot to Teams

1. In Azure Bot → **Channels** → click **Microsoft Teams** → Save
2. In Azure Bot → **Test in Web Chat** — try typing `recent failed jobs` to verify it works
3. To add to a Teams channel:
   - Teams → Apps → Manage your apps → Upload an app
   - Or: in a Teams channel, click **+** → search for your bot

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Bot does not respond | Check ngrok URL matches messaging endpoint in Azure |
| Authentication error | Double-check App ID and client secret |
| "Cannot reach BackupPulse" | Make sure BackupPulse is on port 5000 and BACKUPPULSE_URL is correct |

---

## Note on free tier

Azure Bot F0 = 10,000 messages/month. More than enough for a demo or small team.  
The bot itself (Node.js) runs on your laptop — no Azure hosting needed for demo.
