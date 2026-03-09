/**
 * Cloudflare Email Routing Worker
 * Processes incoming emails, extracts credentials, and POSTs to app
 */

export default {
  async email(message, env, ctx) {
    console.log("[WORKER] Email received");
    const from = message.from;
    const to = message.to;
    console.log(`[WORKER] From: ${from}, To: ${to}`);

    // Get email content from message
    let text = "";
    try {
      // Try to get raw content as a stream
      if (message.raw) {
        const reader = message.raw.getReader();
        const chunks = [];
        let result;
        while (!(result = await reader.read()).done) {
          chunks.push(result.value);
        }
        const buffer = new Uint8Array(chunks.reduce((a, b) => a.concat(Array.from(b)), []));
        text = new TextDecoder().decode(buffer);
      } else if (message.text) {
        // Fallback: try the text property
        text = await message.text();
      } else {
        console.log("[WORKER] ERROR: Cannot access email content");
        return;
      }
    } catch (e) {
      console.log(`[WORKER] ERROR: Failed to read email: ${e.message}`);
      return;
    }

    console.log(`[WORKER] Email body length: ${text.length} chars`);

    // Extract job_id from email address (cafwrapped+{8hex}@wrapped.drew.place)
    const match = to.match(/cafwrapped\+([a-f0-9]{8})@/i);
    if (!match) {
      console.log("[WORKER] ERROR: No job ID found in recipient:", to);
      return;
    }

    const jobId = match[1];
    console.log(`[WORKER] Extracted job ID: ${jobId}`);

    // Extract username and password from email body
    console.log("[WORKER] Extracting credentials...");
    const creds = extractCredentials(text);
    if (!creds) {
      console.log("[WORKER] ERROR: Could not extract credentials from email");
      return;
    }

    const { username, password, name } = creds;
    console.log(`[WORKER] Extracted: name=${name}, username=${username}`);

    // POST to app
    const appUrl = env.APP_URL || "https://cafwrapped.drew.place";
    const secret = env.CREDENTIAL_SECRET || "";
    console.log(`[WORKER] Posting to ${appUrl}/api/credentials`);
    console.log(`[WORKER] APP_URL env: ${env.APP_URL}`);
    console.log(`[WORKER] CREDENTIAL_SECRET env: ${secret ? "set" : "NOT SET"}`);

    try {
      const payload = {
        job_id: jobId,
        username,
        password,
        name,
      };
      console.log(`[WORKER] Payload: ${JSON.stringify(payload)}`);

      const response = await fetch(`${appUrl}/api/credentials`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Credential-Secret": secret,
        },
        body: JSON.stringify(payload),
      });

      console.log(`[WORKER] Response status: ${response.status}`);

      if (response.ok) {
        console.log(`[WORKER] SUCCESS: Posted credentials for job ${jobId}`);
      } else {
        const errorText = await response.text();
        console.log(`[WORKER] ERROR: POST failed with status ${response.status}: ${errorText}`);
      }
    } catch (error) {
      console.log(`[WORKER] ERROR: Exception during POST: ${error.message}`);
      console.log(`[WORKER] Stack: ${error.stack}`);
    }
  },
};

function extractCredentials(text) {
  // Look for username: X and password: Y patterns
  const patterns = [
    /username\s*:\s*(\S+)[\s\S]*?password\s*:\s*(\S+)/i,
    /login\s*:\s*(\S+)[\s\S]*?password\s*:\s*(\S+)/i,
  ];

  let username = null;
  let password = null;

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) {
      username = match[1].trim();
      password = match[2].trim();
      break;
    }
  }

  if (!username || !password) {
    return null;
  }

  // Extract name from "Name: ..." (stop at URL or next capital word)
  const nameMatch = text.match(/Name\s*:\s*([A-Za-z\s]+?)(?:https?:\/\/|$)/i);
  const name = nameMatch ? nameMatch[1].trim() : null;

  return {
    username,
    password,
    name,
  };
}
