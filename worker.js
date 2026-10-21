/**
 * Cloudflare Email Worker for *@stu.exposed.
 * Hands every message to the app untouched; parsing and matching happen in
 * the app's /api/inbound so this never needs redeploying for logic changes.
 *
 * Delivery outcome:
 * - 2xx: accepted.
 * - 422: the app read the message and it can never be an invite (unknown recipient,
 *   not from Atrium, no password): reject it permanently.
 * - anything else (5xx, 401 secret mismatch, a redirect from something in front of
 *   the app, a network error): throw, so delivery fails temporarily and the sending
 *   server retries instead of the invite silently disappearing.
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
    if (res.ok) return;
    const location = res.headers.get("location");
    const detail = `app returned ${res.status} for mail to ${message.to}${location ? ` (redirect to ${location})` : ""}`;
    if (res.status === 422) {
      console.log(`${detail}; rejecting`);
      message.setReject("Not a Flex Bucks guest invite for this address");
      return;
    }
    throw new Error(`${detail}; deferring so the sender retries`);
  },
};
