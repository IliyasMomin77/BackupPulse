// BackupPulse — Microsoft Teams Bot
// Node.js bot using Bot Framework SDK
// Calls the same BackupPulse /chat endpoint as the Google Chat bot
//
// Setup: see SETUP.md in this folder

const restify = require('restify');
const { BotFrameworkAdapter } = require('botbuilder');
const axios = require('axios');

const BACKUPPULSE_URL = process.env.BACKUPPULSE_URL || 'http://localhost:5000/chat';

const adapter = new BotFrameworkAdapter({
  appId: process.env.MicrosoftAppId || '',
  appPassword: process.env.MicrosoftAppPassword || ''
});

adapter.onTurnError = async (context, error) => {
  console.error('[onTurnError]', error);
  await context.sendActivity('Something went wrong. Check that BackupPulse is running.');
};

const server = restify.createServer();
server.use(restify.plugins.bodyParser());

server.post('/api/messages', (req, res) => {
  adapter.processActivity(req, res, async (context) => {
    if (context.activity.type !== 'message') return;

    // Teams wraps mentions in HTML tags like <at>BotName</at> — strip them
    const question = (context.activity.text || '').replace(/<[^>]+>/g, '').trim();

    if (!question) {
      await context.sendActivity(
        'Hi! Ask me about your Veeam backups.\n\n' +
        'Examples:\n' +
        '- recent failed jobs\n' +
        '- repository capacity\n' +
        '- list protected VMs\n' +
        '- restore points for WApp03'
      );
      return;
    }

    try {
      const response = await axios.post(BACKUPPULSE_URL, { question }, { timeout: 60000 });
      await context.sendActivity(response.data.answer || 'No answer returned.');
    } catch (e) {
      await context.sendActivity('Cannot reach BackupPulse: ' + e.message);
    }
  });
});

const PORT = process.env.PORT || 3978;
server.listen(PORT, () => {
  console.log(`BackupPulse Teams bot listening on port ${PORT}`);
  console.log(`BackupPulse URL: ${BACKUPPULSE_URL}`);
});
