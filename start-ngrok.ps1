# start-ngrok.ps1 — expose BackupPulse to Microsoft Teams via ngrok
#
# FIRST-TIME SETUP (do once):
#   1. Sign up free at https://ngrok.com
#   2. Copy your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
#   3. Run: ngrok config add-authtoken <your-token>
#   4. Find your free permanent domain at https://dashboard.ngrok.com/domains
#   5. Set $NGROK_DOMAIN below to that domain (e.g. "yourname.ngrok-free.app")
#   6. In Teams, create an Outgoing Webhook pointed at:
#      https://<your-domain>/api/teams
#   7. Paste the security token Teams gives you into config_final.env → TEAMS_WEBHOOK_SECRET

$NGROK_DOMAIN = "YOUR-DOMAIN.ngrok-free.app"   # <-- replace with your permanent domain
$FLASK_PORT   = 5000

if ($NGROK_DOMAIN -eq "YOUR-DOMAIN.ngrok-free.app") {
    Write-Host ""
    Write-Host "ERROR: Set your ngrok domain first." -ForegroundColor Red
    Write-Host "Edit start-ngrok.ps1 and replace YOUR-DOMAIN.ngrok-free.app" -ForegroundColor Yellow
    Write-Host "Find your free domain at: https://dashboard.ngrok.com/domains" -ForegroundColor Cyan
    exit 1
}

Write-Host ""
Write-Host "Starting ngrok tunnel..." -ForegroundColor Cyan
Write-Host "Teams webhook URL: https://$NGROK_DOMAIN/api/teams" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

ngrok http $FLASK_PORT --domain=$NGROK_DOMAIN
