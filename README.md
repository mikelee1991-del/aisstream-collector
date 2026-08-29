# BoatBoard AIS relay (Cloudflare Worker)

GitHub **Pages cannot** run a WebSocket relay (static hosting only). GitHub **Actions** are ephemeral and are not a substitute. This Worker is a free, always-on `wss://` proxy to [AISStream](https://aisstream.io) so phones work **without** a Pi/laptop left on.

The **site** opens AISStream using a Cloudflare Worker secret. Phones never paste, store, or send that credential.

## Access control (do not re-open)

The live dashboard is a public GitHub Pages app. **Do not** set `Access-Control-Allow-Origin: *` and **do not** put the AISStream credential (or any new shared secret) in Pages JS — a public token is not a control.

The Worker **fails closed**:

| Check | Rule |
| --- | --- |
| Origin | Allow `https://mikelee1991-del.github.io`, `https://boatboard.mikelee1.workers.dev`, and `http(s)://localhost` / `127.0.0.1` / `::1` (any port). Missing Origin, `null` (`file://`), or any other site → **403**. |
| Extra origins | Optional Worker var / env `ALLOWED_ORIGINS` — comma-separated **exact** origins for forks. |
| CORS | Echo the allowlisted Origin only. Never `*`. |
| Rate limit | **20** HTTP/WebSocket requests per IP per 60s (`RELAY_RATE_LIMITER`). **5** `/probe` calls per IP per 60s (`PROBE_RATE_LIMITER`). Best-effort **4** concurrent sockets per IP per isolate. |
| `/probe` | Same Origin + rate limit. It still tests Worker → AISStream; it is not a world-open invitation. |
| Site secret | If the Worker secret is missing, the socket fails closed with a short human note. No key prompt. No raw AISStream error. |

Browsers set `Origin` on WebSocket upgrades and cannot forge it from page JS. `curl` / `websocat` can spoof the header — rate limits still apply.

Local dashboard testing: serve `index.html` from `http://localhost` (a `file://` page sends Origin `null` and is rejected).

Policy lives in `src/access.js` and is mirrored by `../ais-relay.mjs`.

## One-time deploy (~5 minutes)

> **Important (mobile):** This Worker must open AISStream *after* the phone sends its subscription (AISStream closes idle upstream sockets in 3s). Older Worker builds opened upstream too early — phones could sit on “Subscribed — waiting…” forever. Redeploy after pulling latest `src/worker.js`.

1. Free accounts: [Cloudflare](https://dash.cloudflare.com/sign-up) + [AISStream](https://aisstream.io)
2. Install Node.js, then from this folder:

```bash
cd ais-relay-worker
npx wrangler login
npx wrangler deploy
```

Requires Wrangler **≥ 4.36** (Rate Limit bindings).

3. Wrangler prints a URL like `https://boatboard-ais.<you>.workers.dev`
4. Store the AISStream credential as a **Worker secret** (never `[vars]`, never Pages JS, never git):

```bash
npx wrangler secret put AISSTREAM_API_KEY
```

The hosted Worker for this repo already has that secret. Do not print it.

5. In BoatBoard **⚙ Settings**:
   - **AIS relay URL** — leave blank if using this repo’s baked default `wss://boatboard-ais.mikelee1.workers.dev`, or paste your Worker URL to override

BoatBoard rewrites `https://` → `wss://` automatically. The Pages app already sends the correct `Origin`; no client secret is required or accepted.

If you deploy a fork under another GitHub Pages host, set `ALLOWED_ORIGINS` in `wrangler.toml` `[vars]` to that origin before deploy.

**Never commit API keys** to the repo.

## After deploy

- Leave it — Cloudflare keeps the Worker available on the free tier.
- Health check (must send an allowed Origin):

```bash
curl -s -H "Origin: https://mikelee1991-del.github.io" https://boatboard-ais.<you>.workers.dev/probe
```

A green probe does **not** prove vessels are flowing (AISStream has had outages where sockets stay open with zero frames).

- `curl` **without** Origin, or with `Origin: https://evil.example`, must be **403**.
- No cloudflared, no home PC, no Raspberry Pi required for normal use.
- Optional: set `AIS_HOSTED_RELAY_DEFAULT` in `index.html` to your `wss://….workers.dev` URL so every device inherits it after you ship a Pages update (still don’t commit secrets).

Returns JSON `{ ok, ms, detail }`. `ok: false` means this Worker cannot open a WebSocket to AISStream (provider outage or CF→AISStream block) — BoatBoard will show that in the AIS status bar after reconnect attempts.

### Confirm a stranger cannot ride the relay

```bash
# no Origin
curl -sI https://boatboard-ais.<you>.workers.dev/probe

# random site
curl -sI -H "Origin: https://evil.example" https://boatboard-ais.<you>.workers.dev/probe

# WebSocket from a random origin (expect HTTP 403, not 101)
curl -sI \
  -H "Origin: https://evil.example" \
  -H "Upgrade: websocket" \
  -H "Connection: Upgrade" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Sec-WebSocket-Version: 13" \
  https://boatboard-ais.<you>.workers.dev/
```

Then open https://mikelee1991-del.github.io/boatboard/ → AIS tab and confirm live traffic still flows.

## Local / LAN alternative

Power users can still run `../ais-relay.mjs` on a laptop (see root README). Same Origin + rate-limit policy. Put the site credential on that machine (environment), not on the phone. That path needs the machine online (or a temporary cloudflared tunnel).

## How it works

```
Phone (HTTPS BoatBoard)
  └─ wss://boatboard-ais.*.workers.dev   ← this Worker (Origin + rate limit + site secret)
        └─ wss://stream.aisstream.io     ← AISStream (server-side; browsers blocked)
```
"# aisstream-collector" 
