// BackupPulse — Google Chat Bot
// Paste this entire file into Google Apps Script (script.google.com)
// Then follow SETUP.md to connect it to Google Chat API

var BACKUPPULSE_URL = 'https://YOUR-NGROK-URL.ngrok-free.app/chat';

function onMessage(event) {
  // argumentText is the message with the @mention stripped already
  var question = (event.message.argumentText || event.message.text || '').trim();

  if (!question) {
    return {
      text: 'Hi! Ask me about your backups.\n\nExamples:\n• recent failed jobs\n• repository capacity\n• list protected VMs\n• restore points for WApp03'
    };
  }

  try {
    var response = UrlFetchApp.fetch(BACKUPPULSE_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ question: question }),
      muteHttpExceptions: true
    });

    if (response.getResponseCode() !== 200) {
      return { text: 'BackupPulse is not reachable (HTTP ' + response.getResponseCode() + '). Check that the app is running and ngrok is active.' };
    }

    var data = JSON.parse(response.getContentText());
    return { text: data.answer || 'No answer returned.' };

  } catch (e) {
    return { text: 'Error reaching BackupPulse: ' + e.message };
  }
}

function onAddToSpace(event) {
  return {
    text: 'BackupPulse connected!\n\nAsk me anything about your Veeam backups:\n• recent failed jobs\n• repository capacity\n• VMs missing backups\n• job sessions today'
  };
}

function onRemoveFromSpace(event) {
  Logger.log('BackupPulse bot removed from space: ' + event.space.displayName);
}
