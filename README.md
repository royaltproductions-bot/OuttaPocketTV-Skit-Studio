# OuttaPocketTV Skit Studio — stateless production pilot

A real working web storefront that generates a free watermarked AI scene, then sells a custom hand-produced comedy skit ($29.99). This code is independent of the Agent-Operations X-Men engines.

## Infrastructure

- **GitHub:** public source code only. Never store keys, customer data, or generated customer media.
- **Render:** one web service, free plan possible. It runs the secure website/API but stores no customer data on disk. Free Render can sleep and has no uptime guarantee.
- **Stripe:** the payment and order ledger: hosted checkout, customer email, skit idea, style, instructions, payment status, order-fulfilled timestamp. Server verifies order state directly against Stripe; never trusts the browser's payment claims.
- **OpenAI:** customer scene illustrations, branded on our own server with the canonical OuttaPocketTV logo. The generated file is sent to the browser, not retained server-side.
- **Cloudflare Turnstile:** verifies free preview requests. Stripe Customer metadata records one sample per email. Single-process IP limits and a daily cap are additional *temporary in-memory* safeguards, not globally durable rate-limits. Set an OpenAI project budget/alerts before opening free generations. Email aliases, repeated restarts and distributed sources can bypass some safeguards.
- **Fulfillment:** owner creates the actual video, sends MP4 by email, and records completed delivery in Stripe using `/admin`. No video-generation charges until an order is placed.

## Privacy and security

No local SQLite database, persistent volume or uploaded MP4s. Customer ideas and email go to Stripe in order/customer records. Scene text (not email) goes to OpenAI, plus a pseudonymous identifier. Turnstile receives verification requests. Never put any secret or customer data in the **public repository**. Owner access uses a private `ADMIN_TOKEN` bearer secret; no client-provided prices. HTML has CSP, security headers, strict input lengths and signed 72-hour preview tokens. Signed tokens are not encrypted; treat them as customer-private browser data.

## Local smoke test

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python server.py
```

Without API credentials the landing page and admin view render, but paid checkout and AI previews are deliberately OFF. Tests mock external charges and image creation; they do not make real API calls.

## Render

Deploy from the connected **dedicated** GitHub repository. Use the included `Dockerfile` or `render.yaml` blueprint; Render `free` is selected and **no disk** is provisioned. Render deploys commits automatically when the repo is connected. `PUBLIC_BASE_URL` defaults to `RENDER_EXTERNAL_HOSTNAME`.

Set private **Render** environment variables (GitHub Actions secrets do **not** automatically reach Render):

| Name | Required for |
|---|---|
| `STRIPE_SECRET_KEY` | Preview ledger, checkout, paid-order list; use the correct Stripe business |
| `OPENAI_API_KEY` | AI free preview generation, separately billed |
| `TURNSTILE_SITE_KEY` | Public challenge widget |
| `TURNSTILE_SECRET_KEY` | Verify requests before generating AI images |
| `APP_SIGNING_SECRET` | Sign checkout entitlement tokens (use 32+ random bytes) |
| `ADMIN_TOKEN` | Private owner dashboard (use different random value) |
| `PREVIEW_LIMIT_DAILY=5` | Conservative daily per-instance backstop |
| `OPENAI_IMAGE_MODEL=gpt-image-2.5-flare` | Explicit image model |

Optional: `TURNSTILE_HOSTNAME` to match production host, `PUBLIC_BASE_URL`, `SUPPORT_EMAIL`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL` (contact API only). Live website **does not** call Stripe or OpenAI until their private service credentials exist.

`/healthz` is the service health check. `/api/config` reports provider readiness without exposing keys. `/admin` shows paid orders after owner token entry; clicking **EMAIL CUSTOMER** opens a prefilled mail draft, to which the owner attaches the finished MP4. Click **MARK DELIVERED** only *after* actually sending it; that updates Stripe metadata. No automatic MP4 file delivery or email notifications are claimed.

## Customer policies to confirm before taking real payments

- Pricing currently $29.99 for one **10–15-second** original vertical comedy skit.
- Delivery is manual by email. Confirm actual turn-around expectation and business support email before launch.
- Stripe receipt emails can be enabled in the selected Stripe account. Refund declined orders manually in Stripe.
- Privacy and Terms in the footer are plain-language drafts; have counsel review for live consumer sales.
- Before enabling free previews at scale, enable OpenAI account spending alerts and add stronger abuse protection if needed (Turnstile alone cannot guarantee one preview per actual person).

## Not an automatic live launch

Repository tests do not prove the correct Stripe business is configured, that Turnstile is valid for the deployed host, that the OpenAI image model has billing access, or that a real payment succeeds. Test all four before advertising the TikTok URL. Render free tier may sleep after inactivity and is **not** suitable for high availability.
