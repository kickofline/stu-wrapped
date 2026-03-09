/**
 * Cloudflare Email Routing Worker
 * Processes incoming emails, extracts credentials, and POSTs to app
 */

export default {
  async email(message, env, ctx) {
    const from = message.from;
    const to = message.to;
    const raw = await message.raw();
    const text = raw.toString();

    // Extract job_id from email address (cafwrapped+{8hex}@wrapped.drew.place)
    const match = to.match(/cafwrapped\+([a-f0-9]{8})@/i);
    if (!match) {
      console.log("No job ID found in recipient:", to);
      return;
    }

    const jobId = match[1];
    console.log(`Processing credentials for job ${jobId}`);

    // Extract username and password from email body
    const creds = extractCredentials(text);
    if (!creds) {
      console.log("Could not extract credentials from email");
      return;
    }

    const { username, password } = creds;
    console.log(`Extracted credentials for ${username}`);

    // POST to app
    const appUrl = env.APP_URL || "https://cafwrapped.drew.place";
    try {
      const response = await fetch(`${appUrl}/api/credentials`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Credential-Secret": env.CREDENTIAL_SECRET || "",
        },
        body: JSON.stringify({
          job_id: jobId,
          username,
          password,
        }),
      });

      if (response.ok) {
        console.log(`Successfully posted credentials for job ${jobId}`);
      } else {
        console.error(
          `Failed to post credentials: ${response.status}`,
          await response.text()
        );
      }
    } catch (error) {
      console.error("Error posting credentials:", error);
    }
  },
};

function extractCredentials(text) {
  // Look for username: X and password: Y patterns
  const patterns = [
    /username\s*:\s*(\S+)[\s\S]*?password\s*:\s*(\S+)/i,
    /login\s*:\s*(\S+)[\s\S]*?password\s*:\s*(\S+)/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) {
      return {
        username: match[1].trim(),
        password: match[2].trim(),
      };
    }
  }

  return null;
}
