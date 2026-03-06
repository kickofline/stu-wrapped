/* Polls /status/<job_id> every 2 seconds and updates the waiting page. */

const statusMsg  = document.getElementById('status-msg');
const progressEl = document.getElementById('progress-fill');
const stepNum    = document.getElementById('step-num');
const errorBox   = document.getElementById('error-box');
const errorMsg   = document.getElementById('error-msg');
const spinner    = document.getElementById('spinner');

let intervalId = null;

function updateUI(data) {
  const pct = data.total_steps > 0
    ? Math.round((data.step / data.total_steps) * 100)
    : 0;

  if (statusMsg)  statusMsg.textContent  = data.message || '';
  if (progressEl) progressEl.style.width = pct + '%';
  if (stepNum)    stepNum.textContent    = data.step;
}

function poll() {
  fetch('/status/' + JOB_ID)
    .then(r => r.json())
    .then(data => {
      updateUI(data);

      if (data.status === 'done' && data.redirect) {
        clearInterval(intervalId);
        window.location.href = data.redirect;
        return;
      }

      if (data.status === 'error') {
        clearInterval(intervalId);
        if (spinner)  spinner.style.display  = 'none';
        if (errorBox) errorBox.style.display = '';
        if (errorMsg) errorMsg.textContent   = data.error || data.message || 'Unknown error';
      }
    })
    .catch(() => {
      // Network error — keep retrying silently
    });
}

// Start polling
intervalId = setInterval(poll, 2000);
poll(); // immediate first check
