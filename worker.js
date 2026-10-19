/**
 * Cloudflare Email Worker for *@stu.exposed.
 * Hands every message to the app untouched; parsing and matching happen in
 * the app's /api/inbound so this never needs redeploying for logic changes.
 */
export default {
  async email(message, env) {
    const res = await fetch(`${env.APP_URL}/api/inbound`, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "X-Credential-Secret": env.CREDENTIAL_SECRET,
        "X-Envelope-To": message.to,
      },
      body: await new Response(message.raw).arrayBuffer(),
      // A redirect means something (e.g. Cloudflare Access) intercepted the request.
      redirect: "manual",
    });
    if (!res.ok) {
      const location = res.headers.get("location");
      console.log(`app returned ${res.status} for mail to ${message.to}${location ? ` (redirect to ${location})` : ""}`);
    }
  },
};
