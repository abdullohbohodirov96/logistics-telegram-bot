/**
 * Instant-dispatch webhook trigger for the logistics bot.
 *
 * Setup (one-time):
 *   1. Open the Google Sheet -> Extensions -> Apps Script.
 *   2. Delete any existing code in Code.gs, paste this whole file in.
 *   3. Fill in WEBHOOK_URL and WEBHOOK_SECRET below.
 *   4. Run setupTrigger() ONCE from the Apps Script editor (select it in
 *      the function dropdown, click Run). Approve the permissions prompt.
 *   5. Done — from now on, whenever anyone sets a row's Status column to
 *      "SEND" in one of the branch order sheets, the bot is notified
 *      instantly instead of waiting for the next periodic check.
 *
 * This only fires on the Status column, and only when the new value is
 * exactly "SEND" — every other edit (any other column, any other status)
 * is ignored, so it's safe to leave running permanently.
 */

var WEBHOOK_URL = 'https://YOUR-RENDER-BOT-URL.onrender.com/webhook/sheet-edit';
var WEBHOOK_SECRET = 'PASTE_THE_SAME_SECRET_YOU_SET_IN_RENDER_HERE';

function onEditInstallable(e) {
  try {
    var range = e.range;
    var sheet = range.getSheet();
    var sheetName = sheet.getName();

    // Only look at edits inside the order sheets you actually dispatch
    // from. IMPORTANT: replace these with your ACTUAL tab names (check the
    // tabs at the bottom of the spreadsheet) — these are just placeholders.
    var WATCHED_SHEETS = ['orders', 'Qorasaroy orders'];
    if (WATCHED_SHEETS.indexOf(sheetName) === -1) return;

    var lastCol = sheet.getLastColumn();
    var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
    var statusCol = -1;
    for (var i = 0; i < headers.length; i++) {
      var h = String(headers[i]).trim().toLowerCase();
      if (h === 'status' || h === 'holat') { statusCol = i + 1; break; }
    }
    if (statusCol === -1) return;
    if (range.getColumn() !== statusCol) return;

    var rowIndex = range.getRow();
    if (rowIndex === 1) return; // header row

    var newValue = String(range.getValue()).trim().toUpperCase();
    if (newValue !== 'SEND') return;

    var payload = {
      secret: WEBHOOK_SECRET,
      sheet_name: sheetName,
      row_index: rowIndex
    };

    UrlFetchApp.fetch(WEBHOOK_URL, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    Logger.log('onEditInstallable error: ' + err);
  }
}

/** Run this ONCE manually to install the trigger. */
function setupTrigger() {
  // Remove any old triggers for this function first, so re-running is safe.
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'onEditInstallable') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  ScriptApp.newTrigger('onEditInstallable')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onEdit()
    .create();
  Logger.log('Trigger installed.');
}
