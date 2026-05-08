# WhatsApp QR — Architecture & Strategies

**Purpose:** Document the architecture and strategies for the WhatsApp QR flow. No code changes — reference only.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  CLIENT LAYER                                                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • Streamlit Cloud (HTTPS) — remote UI, calls API with WA_QR_BRIDGE_TOKEN        │
│  • wa-qr-ui (optional) — local Streamlit on VM:8501, same flow                   │
│  • Direct curl — for testing                                                     │
└─────────────────────────────────┬───────────────────────────────────────────────┘
                                  │ HTTP (Bearer token)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  API LAYER (FastAPI :8000) — Publicly exposed                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Bridge endpoints (require Authorization: Bearer WA_QR_BRIDGE_TOKEN):            │
│    GET  /admin/wa/status   → proxy to whatsapp-bot /health                       │
│    GET  /admin/wa/qr       → read Redis cache OR proxy to whatsapp-bot /qr       │
│    POST /admin/wa/reconnect → proxy to whatsapp-bot /reconnect                   │
│  Public aliases: /wa/status, /wa/qr, /wa/connect (same logic, X-API-Key auth)   │
│                                                                                  │
│  Background: wa_keepalive loop (every ~25s)                                      │
│    → GET bot/health; if disconnected → POST bot/reconnect; poll bot/qr; cache   │
│                                                                                  │
│  Redis cache: wa:last_qr, wa:last_qr_ts (TTL ~120s)                              │
│    → Bridge stores QR from bot; GET /admin/wa/qr checks Redis first              │
└─────────────────────────────────┬───────────────────────────────────────────────┘
                                  │ Internal HTTP (Docker network)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHATSAPP-BOT LAYER (Node/Baileys :3100) — Internal only, NOT exposed            │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Endpoints:                                                                      │
│    GET  /health    → { status: "ok" }                                            │
│    GET  /status    → { connected, status, lastDisconnectReason }                 │
│    GET  /qr        → { status, qr, expires_in } — returns in-memory qrValue      │
│    POST /reconnect → 200 immediately; async: clearAuthFolder, connect()          │
│                                                                                  │
│  Storage: /data/wa-auth (volume)                                                 │
│    • Baileys auth state (useMultiFileAuthState)                                  │
│    • last_qr.json — persisted QR for restarts                                    │
│                                                                                  │
│  Baileys flow:                                                                   │
│    connect() → makeWASocket() → sock.ev.on('connection.update')                  │
│    WhatsApp servers send QR via up.qr → set qrValue, write last_qr.json          │
│    On connection failure: Connection Failure (WebSocket to WhatsApp fails)       │
└─────────────────────────────────┬───────────────────────────────────────────────┘
                                  │ WebSocket (Baileys → WhatsApp Web servers)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WHATSAPP SERVERS (External — Meta/WhatsApp)                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • Baileys opens WebSocket to WhatsApp Web infrastructure                        │
│  • Handshake (noise protocol) → if success, WhatsApp sends QR in connection.update│
│  • Connection Failure = handshake/WebSocket fails before QR is received          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Flow (QR Generation)

| Step | Actor | Action |
|------|-------|--------|
| 1 | User / Streamlit | POST /admin/wa/reconnect (with Bearer token) |
| 2 | API | POST http://whatsapp-bot:3100/reconnect |
| 3 | whatsapp-bot | Returns 200 immediately; async: clearAuthFolder(), connect() |
| 4 | whatsapp-bot | makeWASocket() — Baileys opens WebSocket to WhatsApp |
| 5 | WhatsApp | Sends QR via connection.update event (if handshake succeeds) |
| 6 | whatsapp-bot | Sets qrValue, writes last_qr.json, saveQrState() |
| 7 | User / Streamlit | GET /admin/wa/qr (poll every few seconds) |
| 8 | API | Checks Redis → miss → GET http://whatsapp-bot:3100/qr |
| 9 | whatsapp-bot | Returns { qr: qrValue, status: "qr_ready" } |
| 10 | API | Caches in Redis, returns to client |
| 11 | User | Scans QR in WhatsApp app → Baileys receives connection open |

**Failure point (your case):** Step 5 never happens. Baileys WebSocket fails with "Connection Failure" before WhatsApp sends the QR. So qrValue stays null, and /qr returns `{ qr: null, status: "not_ready" }`.

---

## 3. Strategies in Use

### 3.1 Security
- **Bridge token:** WA_QR_BRIDGE_TOKEN — Bearer auth for /admin/wa/*. Same value in VM .env and Streamlit Secrets.
- **No direct exposure:** whatsapp-bot is NOT exposed to the internet. Only API talks to it.
- **CORS:** STREAMLIT_ORIGIN added for Streamlit Cloud.

### 3.2 Caching
- **Redis:** API caches QR (wa:last_qr, wa:last_qr_ts) with TTL. Reduces load on bot; QR survives short polling gaps.
- **Bot disk:** last_qr.json — survives bot restart; QR expires in ~60–120s.

### 3.3 Reconnect
- **Manual:** POST /admin/wa/reconnect — clears auth, calls connect() again.
- **Background (keepalive):** API task checks bot health; if disconnected, triggers reconnect and polls for QR, then caches.

### 3.4 Error Handling
- **Connection Failure:** Baileys cannot complete WebSocket handshake with WhatsApp. Occurs before QR. Common causes: network block, firewall, WhatsApp blocking IP (VPS/datacenter).

---

## 4. Where "Connection Failure" Occurs

```
[Streamlit] → [API] → [whatsapp-bot] → [Baileys] → [WhatsApp Servers]
                                    ↑
                                    Failure here
```

- **Baileys** connects to WhatsApp over WebSocket.
- **Connection Failure** = WebSocket handshake or noise protocol fails.
- **Result:** QR never arrives; qrValue stays null.

**Typical causes:**
1. **Outbound blocked:** VM/firewall blocks HTTPS/WebSocket to WhatsApp.
2. **IP blocked by WhatsApp:** Many cloud/VPS IPs are blocked.
3. **Proxy required:** Some hosts need HTTP/SOCKS proxy to reach WhatsApp.
4. **Baileys/WhatsApp protocol change:** Library may need update.

---

## 5. Alternative Strategies (Not Implemented)

| Strategy | Description | When to Consider |
|----------|-------------|------------------|
| **Proxy** | Route Baileys traffic via HTTP/SOCKS proxy | When VM IP is blocked by WhatsApp |
| **Different host** | Run whatsapp-bot on a different server (e.g. home, different cloud) | When current provider blocks WhatsApp |
| **Paired device / official API** | Use WhatsApp Business API instead of Baileys | For production without QR flow |
| **Tunnel (ngrok/Cloudflare)** | Expose bot on public URL for testing | Only for dev; not for production |

---

## 6. Summary

- **Architecture:** Streamlit/UI → API (bridge) → whatsapp-bot (Baileys) → WhatsApp.
- **QR path:** Reconnect → Bot connect() → Baileys WebSocket → WhatsApp sends QR → Bot stores → API caches → Client polls.
- **Your failure:** WebSocket to WhatsApp fails (Connection Failure) before QR; no code bug in the bridge or caching.
- **Fix direction:** Ensure whatsapp-bot can reach WhatsApp (network, firewall, proxy, or different host). Clearing session / restarting only helps if the cause was corrupted state; it does not fix network blocks.
