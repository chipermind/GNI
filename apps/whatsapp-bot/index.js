/**
 * WhatsApp QR bot — serves /health, /status, /qr, POST /reconnect for API bridge.
 * Auth state is persisted under /data/wa-auth (volume-mounted); QR to last_qr.json + Redis wa:last_qr.
 * When connected: Redis wa:connected = true; on disconnect QR keys cleared and wa:connected = false.
 * AUTH_FOLDER, PORT, REDIS_URL from env. Process never exits on disconnect; keep-alive prevents clean exit.
 */
const express = require('express');
const fs = require('fs');
const https = require('https');
const path = require('path');
const { default: makeWASocket, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const pino = require('pino');

const logger = pino({ level: process.env.LOG_LEVEL || 'info' });
const PORT = parseInt(process.env.PORT || '3100', 10);
// Ensure AUTH_FOLDER is EXACTLY /data/wa-auth (required by docker volume mount)
const AUTH_FOLDER = process.env.AUTH_FOLDER || '/data/wa-auth';
const QR_EXPIRY_SECONDS = 120; // QR expires in 120 seconds

// Redis keys for API bridge (GET /wa/qr reads from cache)
const WA_QR_KEY = 'wa:last_qr';
const WA_QR_TS_KEY = 'wa:last_qr_ts';
const WA_CONNECTED_KEY = 'wa:connected';

let redisClientPromise = null;
function getRedisClient() {
  if (!process.env.REDIS_URL) return null;
  if (redisClientPromise !== null && redisClientPromise !== undefined) return redisClientPromise;
  try {
    const { createClient } = require('redis');
    const c = createClient({ url: process.env.REDIS_URL });
    c.on('error', () => {});
    redisClientPromise = c.connect().then(() => c).catch(() => null);
  } catch (e) {
    redisClientPromise = Promise.resolve(null);
  }
  return redisClientPromise;
}

/** Write QR to Redis (wa:last_qr, wa:last_qr_ts) for API bridge. Fire-and-forget. */
function writeQrToRedis(qr, ts) {
  const p = getRedisClient();
  if (!p) return;
  p.then((client) => {
    if (!client) return;
    const ttl = qr ? QR_EXPIRY_SECONDS : 1;
    if (qr) {
      return Promise.all([
        client.setEx(WA_QR_KEY, ttl, qr),
        client.setEx(WA_QR_TS_KEY, ttl, String(ts)),
      ]);
    }
    return client.del(WA_QR_KEY, WA_QR_TS_KEY);
  }).catch(() => {});
}

/** Set wa:connected in Redis (true/false). Fire-and-forget. */
function setRedisConnected(connected) {
  const p = getRedisClient();
  if (!p) return;
  p.then((client) => {
    if (!client) return;
    return client.set(WA_CONNECTED_KEY, connected ? 'true' : 'false');
  }).catch(() => {});
}

// Helper for writing QR state file (matches exact format requested)
const QR_FILE = path.join(AUTH_FOLDER, 'last_qr.json');
const nowTs = () => Math.floor(Date.now() / 1000);

function writeLastQr(payload) {
  try {
    fs.mkdirSync(AUTH_FOLDER, { recursive: true });
    const tmp = `${QR_FILE}.tmp.${process.pid}.${Date.now()}`;
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf-8');
    fs.renameSync(tmp, QR_FILE);
  } catch (e) {
    logger.warn('writeLastQr: %s', e.message);
  }
}

const app = express();
app.use(express.json());
let sock = null;
let qrValue = null;
let qrTimestamp = null;
let lastDisconnectReason = null;
let lastStatusCode = null;
let lastSeenQrAt = null;
let connected = false;
let connecting = false;
let phoneNumber = null;
let connectionState = 'disconnected'; // disconnected, connecting, qr_ready, connected

// Production reconnection policy: backoff (max 60s) and cooldown after 3 Connection Failures in 2 min
const CONNECTION_FAILURE_WINDOW_MS = 2 * 60 * 1000;
const CONNECTION_FAILURE_COOLDOWN_MS = 10 * 60 * 1000;
const BACKOFF_MAX_SECONDS = 60;
let connectionFailureTimestamps = [];
let cooldownUntil = 0;
let reconnectBackoffAttempt = 0;

function isConnectionFailure(up) {
  const reason = up.lastDisconnectReason;
  if (reason == null) return false;
  const str = typeof reason === 'string' ? reason : (reason.error?.message || reason.message || JSON.stringify(reason));
  return /connection\s+failure/i.test(str);
}

function pruneConnectionFailureTimestamps() {
  const cutoff = Date.now() - CONNECTION_FAILURE_WINDOW_MS;
  connectionFailureTimestamps = connectionFailureTimestamps.filter((t) => t > cutoff);
}

function recordConnectionFailure() {
  connectionFailureTimestamps.push(Date.now());
  pruneConnectionFailureTimestamps();
  if (connectionFailureTimestamps.length >= 3) {
    cooldownUntil = Date.now() + CONNECTION_FAILURE_COOLDOWN_MS;
    logger.info({ event: 'WA_COOLDOWN', cooldown_minutes: 10 }, 'WA_COOLDOWN: 3 Connection Failures in 2 min; reconnect paused 10 min');
  }
}

function getBackoffDelayMs() {
  const sec = Math.min(Math.pow(2, reconnectBackoffAttempt), BACKOFF_MAX_SECONDS);
  return sec * 1000;
}

/**
 * Extract statusCode, reason string, and stack snippet from Baileys lastDisconnectReason.
 * lastDisconnectReason can be { error: { output: { statusCode }, message, stack }, connection } or a string.
 */
function parseLastDisconnect(ld) {
  const out = { statusCode: null, reason: null, stackTraceSnippet: null };
  if (ld == null) return out;
  if (typeof ld === 'string') {
    out.reason = ld;
    return out;
  }
  const err = ld.error || ld;
  if (err && typeof err === 'object') {
    out.statusCode = err.output?.statusCode ?? err.statusCode ?? null;
    out.reason = err.message || err.details || JSON.stringify(ld).slice(0, 500);
    const stack = err.stack || err.stackTrace;
    out.stackTraceSnippet = typeof stack === 'string' ? stack.slice(0, 400) : null;
  } else {
    out.reason = JSON.stringify(ld).slice(0, 500);
  }
  return out;
}

/**
 * Single JSON log per event: event, status, statusCode, reason, retryCount, delayMs, stackTraceSnippet.
 */
function logStructured(payload) {
  const line = {
    event: payload.event,
    status: payload.status ?? connectionState,
    statusCode: payload.statusCode ?? lastStatusCode,
    reason: payload.reason ?? (typeof lastDisconnectReason === 'string' ? lastDisconnectReason : (lastDisconnectReason?.error?.message || null)),
    retryCount: payload.retryCount ?? reconnectBackoffAttempt,
    delayMs: payload.delayMs ?? (payload.event === 'DISCONNECTED' || payload.event === 'WA_BACKOFF' ? getBackoffDelayMs() : null),
    stackTraceSnippet: payload.stackTraceSnippet ?? null,
  };
  logger.info(line, 'WA_EVENT');
}

/**
 * Load persisted QR state from file.
 */
function loadQrState() {
  try {
    if (fs.existsSync(QR_FILE)) {
      const content = fs.readFileSync(QR_FILE, 'utf-8');
      const state = JSON.parse(content);
      const now = Math.floor(Date.now() / 1000);
      
      // Check if QR is still valid (not expired)
      if (state.status === 'qr_ready' && state.qr && state.expires_at) {
        if (now < state.expires_at) {
          qrValue = state.qr;
          qrTimestamp = state.updated_at * 1000; // Convert to ms
          connectionState = 'qr_ready';
          logger.info({ event: 'QR_LOADED_FROM_DISK' }, 'Loaded QR from disk (expires in %ds)', state.expires_at - now);
          return;
        } else {
          logger.info({ event: 'QR_EXPIRED' }, 'QR from disk expired, clearing');
        }
      } else if (state.status === 'connected') {
        connectionState = 'disconnected'; // Will reconnect on startup
        logger.info({ event: 'STATE_LOADED' }, 'Previous state: connected, will reconnect');
        return;
      }
    }
  } catch (e) {
    logger.warn({ event: 'QR_STATE_LOAD_ERROR' }, 'Failed to load QR state: %s', e.message);
  }
  // Default: no valid QR
  qrValue = null;
  qrTimestamp = null;
}

/**
 * Persist QR state to file.
 */
function saveQrState(status, qr = null, reason = null) {
  try {
    const now = Math.floor(Date.now() / 1000);
    const state = {
      qr: qr,
      status: status,
      expires_at: status === 'qr_ready' && qr ? now + QR_EXPIRY_SECONDS : null,
      updated_at: now,
      lastDisconnectReason: reason || null,
    };
    
    writeLastQr(state);
    logger.debug({ event: 'QR_STATE_SAVED', status }, 'Saved QR state: %s', status);
  } catch (e) {
    logger.warn({ event: 'QR_STATE_SAVE_ERROR' }, 'Failed to save QR state: %s', e.message);
  }
}

function clearAuthFolder() {
  try {
    if (fs.existsSync(AUTH_FOLDER)) {
      for (const f of fs.readdirSync(AUTH_FOLDER)) {
        const p = path.join(AUTH_FOLDER, f);
        // Don't delete last_qr.json, only auth files
        if (f === path.basename(QR_FILE)) {
          continue;
        }
        const stat = fs.statSync(p);
        if (stat.isDirectory()) {
          fs.rmSync(p, { recursive: true });
        } else {
          fs.unlinkSync(p);
        }
      }
    }
  } catch (e) {
    logger.warn({ event: 'CLEAR_AUTH_ERROR' }, 'clearAuthFolder: %s', e.message);
  }
}

async function connect() {
  if (connecting) {
    logger.warn({ event: 'CONNECT_ALREADY_IN_PROGRESS' }, 'connect() called while already connecting, skipping');
    return;
  }
  connecting = true;
  qrValue = null;
  lastDisconnectReason = null;
  lastStatusCode = null;
  connected = false;
  
  try {
    if (sock) {
      try { 
        sock.end(undefined); 
        logger.info({ event: 'SOCKET_CLOSED' }, 'Closed existing socket');
      } catch (e) {
        logger.warn({ event: 'SOCKET_CLOSE_ERROR' }, 'Error closing socket: %s', e.message);
      }
      sock = null;
    }
    
    logger.info({ AUTH_FOLDER }, 'WA_CONNECT_START');
    logStructured({ event: 'WA_CONNECT_START', status: 'connecting', retryCount: reconnectBackoffAttempt });

    // Use AUTH_FOLDER (defaults to /data/wa-auth, set via env in docker-compose)
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_FOLDER);
    logger.info('WA_AUTH_STATE_READY');
    logStructured({ event: 'WA_AUTH_STATE_READY', status: 'connecting' });
    
    sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      logger: pino({ level: 'info' }), // Changed from 'silent' to 'info' for visibility
    });
    
    logger.info('Starting Baileys socket...');

    sock.ev.on('connection.update', (up) => {
      // QR event (do not log or print QR string)
      if (up.qr) {
        qrValue = up.qr;
        qrTimestamp = Date.now();
        lastSeenQrAt = new Date().toISOString();
        connectionState = 'qr_ready';
        const ts = nowTs();
        logger.info({ event: 'QR_READY' }, 'QR_READY');
        logStructured({ event: 'QR_READY', status: 'qr_ready', retryCount: reconnectBackoffAttempt });
        writeLastQr({ qr: up.qr, status: 'qr_ready', expires_at: ts + QR_EXPIRY_SECONDS, updated_at: ts });
        writeQrToRedis(up.qr, ts);
      }

      // Connection state changes
      if (up.connection === 'open') {
        reconnectBackoffAttempt = 0;
        connectionFailureTimestamps = [];
        connected = true;
        qrValue = null;
        qrTimestamp = null;
        connectionState = 'connected';
        logger.info({ event: 'CONNECTED' }, 'CONNECTED');
        logStructured({ event: 'CONNECTED', status: 'connected', retryCount: 0 });
        writeLastQr({ qr: null, status: 'connected', expires_at: 0, updated_at: nowTs() });
        writeQrToRedis(null);
        setRedisConnected(true);
        phoneNumber = up.me?.id?.split(':')[0] || null;
      } else if (up.connection === 'close') {
        lastDisconnectReason = up.lastDisconnectReason || null;
        const parsed = parseLastDisconnect(lastDisconnectReason);
        lastStatusCode = parsed.statusCode;
        connected = false;
        if (isConnectionFailure(up)) {
          recordConnectionFailure();
        }
        connectionState = 'disconnected';
        // Log full disconnect payload for diagnostics (no QR/creds)
        logger.info({
          event: 'DISCONNECTED_PAYLOAD',
          lastDisconnectReason: lastDisconnectReason,
          statusCode: parsed.statusCode,
          reason: parsed.reason,
          output: lastDisconnectReason?.error?.output,
          message: lastDisconnectReason?.error?.message,
          stackSnippet: parsed.stackTraceSnippet,
        }, 'DISCONNECTED full payload');
        logStructured({
          event: 'DISCONNECTED',
          status: 'disconnected',
          statusCode: parsed.statusCode,
          reason: parsed.reason,
          retryCount: reconnectBackoffAttempt,
          delayMs: getBackoffDelayMs(),
          stackTraceSnippet: parsed.stackTraceSnippet,
        });
        logger.info({ reason: lastDisconnectReason, event: 'DISCONNECTED' }, 'DISCONNECTED');
        writeLastQr({ qr: null, status: 'disconnected', lastDisconnectReason, expires_at: 0, updated_at: nowTs() });
        writeQrToRedis(null);
        setRedisConnected(false);
        phoneNumber = null;
      }

      if (up.isNewLogin) {
        logger.info({ event: 'NEW_LOGIN' }, 'New login detected');
        logStructured({ event: 'NEW_LOGIN', status: connectionState });
      }

      // Ensure connecting flag is reset at end of handler
      connecting = false;
    });

    sock.ev.on('creds.update', () => {
      saveCreds();
      logStructured({ event: 'creds.update', status: connectionState });
    });
    
    logger.info({ event: 'SOCKET_CREATED' }, 'Baileys socket created, waiting for connection events...');
    logStructured({ event: 'SOCKET_CREATED', status: 'connecting' });
  } catch (e) {
    connecting = false;
    logger.error({ event: 'CONNECT_FATAL', err: e?.message, stack: e?.stack }, 'connect fatal: %s', e?.stack || e?.message || String(e));
    // Do not rethrow: keep process alive so HTTP server and reconnect path remain available
  }
}

app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    connected,
    last_disconnect_reason: typeof lastDisconnectReason === 'string'
      ? lastDisconnectReason
      : (lastDisconnectReason?.error?.message || (lastDisconnectReason ? JSON.stringify(lastDisconnectReason).slice(0, 500) : null)),
    last_status_code: lastStatusCode,
    last_seen_qr_at: lastSeenQrAt,
    retry_count: reconnectBackoffAttempt,
  });
});

app.get('/netcheck', (req, res) => {
  const serverTime = new Date().toISOString();
  const timeoutMs = 5000;
  let responded = false;

  const send = (payload) => {
    if (responded) return;
    responded = true;
    res.json(payload);
  };

  const hreq = https.request(
    { hostname: 'web.whatsapp.com', path: '/', method: 'HEAD' },
    (hres) => {
      send({ ok: true, status_code: hres.statusCode, error: null, server_time: serverTime });
    }
  );

  hreq.on('error', (err) => {
    send({ ok: false, status_code: null, error: err.message || String(err), server_time: serverTime });
  });

  hreq.on('timeout', () => {
    hreq.destroy();
    send({ ok: false, status_code: null, error: 'timeout', server_time: serverTime });
  });

  hreq.setTimeout(timeoutMs);
  hreq.end();
});

app.get('/debug/auth', (req, res) => {
  // READ ONLY endpoint to confirm file creation
  const result = {
    AUTH_FOLDER: AUTH_FOLDER,
    exists: false,
    files: [],
    last_qr_json: null,
    error: null,
  };
  
  try {
    result.exists = fs.existsSync(AUTH_FOLDER);
    
    if (result.exists) {
      try {
        result.files = fs.readdirSync(AUTH_FOLDER);
      } catch (e) {
        result.error = `readdirSync failed: ${e.message}`;
      }
      
      // Try to read last_qr.json if it exists
      const qrFilePath = path.join(AUTH_FOLDER, 'last_qr.json');
      if (fs.existsSync(qrFilePath)) {
        try {
          const content = fs.readFileSync(qrFilePath, 'utf-8');
          // Cap output length to 2KB
          result.last_qr_json = content.length > 2048 ? content.substring(0, 2048) + '... (truncated)' : content;
        } catch (e) {
          result.error = `readFileSync failed: ${e.message}`;
        }
      }
    }
  } catch (e) {
    result.error = `General error: ${e.message}`;
  }
  
  res.json(result);
});

app.get('/status', (req, res) => {
  const now = Date.now();
  const in_cooldown = cooldownUntil > now;
  const status = connected
    ? 'connected'
    : (qrValue ? 'qr_ready' : (connecting ? 'not_ready' : 'disconnected'));
  res.json({
    connected,
    status,
    lastDisconnectReason: lastDisconnectReason || null,
    last_status_code: lastStatusCode,
    last_seen_qr_at: lastSeenQrAt,
    retry_count: reconnectBackoffAttempt,
    in_cooldown,
    cooldown_until: in_cooldown ? new Date(cooldownUntil).toISOString() : null,
    server_time: new Date().toISOString(),
  });
});

app.get('/qr', (req, res) => {
  const now = Date.now();
  let expiresIn = 0;
  
  if (connected) {
    res.json({
      status: 'connected',
      qr: null,
      expires_in: 0,
      server_time: new Date().toISOString(),
    });
    return;
  }
  
  if (qrValue && qrTimestamp) {
    const elapsed = Math.floor((now - qrTimestamp) / 1000);
    expiresIn = Math.max(0, QR_EXPIRY_SECONDS - elapsed);
    
    if (expiresIn <= 0) {
      // QR expired
      qrValue = null;
      qrTimestamp = null;
      connectionState = 'not_ready';
      saveQrState('not_ready', null);
    }
  }
  
  res.json({
    status: qrValue ? 'qr_ready' : 'not_ready',
    qr: qrValue || null,
    expires_in: expiresIn,
    server_time: new Date().toISOString(),
  });
});

app.post('/reconnect', async (req, res) => {
  const wipe_auth = !!(req.body && req.body.wipe_auth === true);
  res.json({ ok: true, message: 'Reconnect triggered. Poll /qr for QR code.' });

  (async () => {
    try {
      logger.info({ event: 'WA_RECONNECT_REQUESTED', wipe_auth }, 'WA_RECONNECT_REQUESTED');

      if (connecting) {
        logger.warn({ event: 'RECONNECT_ALREADY_IN_PROGRESS' }, 'Reconnect already in progress, skipping');
        return;
      }

      const now = Date.now();
      if (cooldownUntil > now) {
        logger.info({ event: 'WA_COOLDOWN', cooldown_until: new Date(cooldownUntil).toISOString() }, 'WA_COOLDOWN: reconnect skipped (in cooldown)');
        return;
      }

      if (sock) {
        if (wipe_auth) {
          logger.info({ event: 'LOGOUT_START' }, 'wipe_auth=true: logging out and clearing auth');
          try {
            await sock.logout();
            logger.info({ event: 'LOGOUT_SUCCESS' }, 'Logout successful');
          } catch (e) {
            logger.warn({ event: 'LOGOUT_ERROR' }, 'Logout error (continuing): %s', e.message);
            try { sock.end(undefined); } catch (_) {}
          }
        } else {
          logger.info({ event: 'SOCKET_RESTART' }, 'wipe_auth=false: restarting socket without logout');
          try { sock.end(undefined); } catch (_) {}
        }
        sock = null;
        connected = false;
        connectionState = 'disconnected';
        await new Promise((r) => setTimeout(r, 500));
      }

      if (wipe_auth) {
        clearAuthFolder();
        writeLastQr({ qr: null, status: 'disconnected', expires_at: 0, updated_at: nowTs() });
        logger.info({ event: 'WA_AUTH_WIPED' }, 'WA_AUTH_WIPED');
      }

      qrValue = null;
      qrTimestamp = null;
      phoneNumber = null;
      connectionState = 'disconnected';

      const backoffMs = getBackoffDelayMs();
      if (backoffMs > 0) {
        logger.info({ event: 'WA_BACKOFF', delay_seconds: backoffMs / 1000 }, 'WA_BACKOFF');
        logStructured({ event: 'WA_BACKOFF', status: 'disconnected', delayMs: backoffMs, retryCount: reconnectBackoffAttempt });
        await new Promise((r) => setTimeout(r, backoffMs));
      }
      reconnectBackoffAttempt += 1;

      await connect();
      logger.info({ event: 'RECONNECT_IN_PROGRESS' }, 'Reconnect initiated, QR will appear shortly');
    } catch (e) {
      logger.error({ event: 'RECONNECT_ERROR' }, 'reconnect: %s', e.message);
    }
  })();
});

app.post('/reset-session', async (req, res) => {
  try {
    logger.info({ event: 'RESET_SESSION_REQUESTED' }, 'Reset session requested');
    if (sock) {
      try { sock.end(undefined); } catch (_) {}
      sock = null;
    }
    connected = false;
    qrValue = null;
    qrTimestamp = null;
    phoneNumber = null;
    connectionState = 'disconnected';
    clearAuthFolder();
    // Also delete QR state file
    try {
      if (fs.existsSync(QR_FILE)) {
        fs.unlinkSync(QR_FILE);
      }
    } catch (_) {}
    logger.info({ event: 'SESSION_RESET' }, 'Auth folder cleared, exiting process to trigger restart');
    res.json({ ok: true, message: 'Session reset. Process will exit for restart.' });
    setTimeout(() => process.exit(0), 1000);
  } catch (e) {
    logger.error({ event: 'RESET_SESSION_ERROR' }, 'reset-session: %s', e.message);
    res.status(500).json({ ok: false, error: e.message });
  }
});

// Global error handlers (do NOT crash-loop silently)
process.on('unhandledRejection', (err) => {
  console.error('unhandledRejection', err);
  logger.error({ event: 'UNHANDLED_REJECTION' }, err);
});

process.on('uncaughtException', (err) => {
  console.error('uncaughtException', err);
  logger.error({ event: 'UNCAUGHT_EXCEPTION' }, err);
  // Don't exit immediately - let the process manager handle it
});

async function main() {
  // Persist auth state under /data/wa-auth (volume-mounted); useMultiFileAuthState(AUTH_FOLDER) uses it
  loadQrState();

  // Connect (will use persisted auth state if available). Never exits process on failure.
  await connect().catch((e) => {
    logger.warn({ event: 'INITIAL_CONNECT_FAILED' }, 'Initial connect failed (server will stay up): %s', e?.message);
  });

  // Listen on 0.0.0.0 to accept connections from Docker network
  app.listen(PORT, '0.0.0.0', () => {
    console.log('HTTP_SERVER_STARTED', PORT);
    console.log('NETCHECK_AVAILABLE');
    logger.info({ event: 'SERVER_STARTED', port: PORT }, 'WhatsApp bot listening on 0.0.0.0:%d', PORT);
  });

  // Keep event loop alive so process never exits with code 0 when idle
  setInterval(() => {}, 1 << 30);
}

main().catch((e) => {
  console.error('STARTUP_ERROR', e);
  logger.error({ event: 'STARTUP_ERROR', message: e?.message, stack: e?.stack }, 'STARTUP_ERROR: %s', e?.stack || e?.message || e);
  // Still start HTTP server and keep process alive so /reconnect and /health work
  app.listen(PORT, '0.0.0.0', () => {
    logger.info({ event: 'SERVER_STARTED_AFTER_ERROR', port: PORT }, 'WhatsApp bot listening on 0.0.0.0:%d (startup had errors)', PORT);
  });
  setInterval(() => {}, 1 << 30);
});
