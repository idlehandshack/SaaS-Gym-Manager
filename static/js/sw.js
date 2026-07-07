// ============================================================
//  EnterGYM Service Worker v9
//  /api/geo-mark-attendance/ is @csrf_exempt + @login_required
//  so no CSRF token needed — session cookie handles auth.
//  Security: NO gym coords, NO userId, NO client-side check.
// ============================================================

self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', e  => e.waitUntil(self.clients.claim()));

// ── State ────────────────────────────────────────────────────
let isEnrolled      = false;
let userHash        = '';      // opaque per-user hash, no raw PK
let pollTimeoutId   = null;

const BASE_DELAY = 60_000;   // 60s — normal poll interval
const MAX_DELAY  = 300_000;  // 5 min — backoff ceiling
let retryDelay    = BASE_DELAY;

// ── Message handler ──────────────────────────────────────────
self.addEventListener('message', async (event) => {
  const msg = event.data || {};

  switch (msg.type) {

    case 'START_GEO':
      isEnrolled = msg.config?.isEnrolled === true;
      userHash   = msg.config?.userHash   || '';

      if (!isEnrolled) {
        stopPolling();
        break;
      }

      if (pollTimeoutId === null) {
        retryDelay = BASE_DELAY;   // fresh session — start at normal cadence
        scheduleNext(0);           // fire immediately, then keep the cadence
      }
      break;

    case 'REPORT_LOC':
    case 'CACHE_LOC':
      if (isEnrolled) {
        await tryAutoMark(msg.lat, msg.lng);
      }
      break;

    case 'STOP_GEO':
      stopPolling();
      break;
  }
});

function stopPolling() {
  clearTimeout(pollTimeoutId);
  pollTimeoutId = null;
  retryDelay = BASE_DELAY;   // reset so the next START_GEO begins clean
}

// ── Adaptive poll loop ───────────────────────────────────────
function scheduleNext(delay = retryDelay) {
    clearTimeout(pollTimeoutId);
    pollTimeoutId = setTimeout(async () => {
        await requestLocationFromClients();
        if (isEnrolled) {
            scheduleNext(retryDelay);
        }
    }, delay);
}

async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();

    const timer = setTimeout(() => controller.abort(), timeout);

    try {
        return await fetch(url, {
            ...options,
            signal: controller.signal
        });
    } finally {
        clearTimeout(timer);
    }
}

// ── Ask all open tabs for their current GPS position ─────────
async function requestLocationFromClients() {
  if (!isEnrolled) return;
  const clients = await self.clients.matchAll({ type: 'window' });
  if (clients.length === 0) return;
  clients.forEach(c => c.postMessage({ type: 'REQUEST_LOC' }));
}

// ── Drop any flag entries not from today, across all users on
//    this device — anything older is stale and safe to discard.
async function cleanupOldFlags(flagCache, today) {
  const prefix = `att_done_${userHash}_`;
  const keys = await flagCache.keys();
  await Promise.all(
    keys
      .filter(req => req.url.includes(prefix) && !req.url.includes(today))
      .map(req => flagCache.delete(req))
  );
}

// ── Main auto-mark flow ──────────────────────────────────────
async function tryAutoMark(lat, lng) {
  if (lat == null || lng == null) return;

  // Namespace flag by userHash — no bleed between users on same device
  const today   = new Date().toISOString().slice(0, 10);
  const doneKey = `att_done_${userHash}_${today}`;

  const flagCache = await caches.open('att-flags');
  const existing  = await flagCache.match(doneKey);
  if (existing) return;   // already marked today — skip

  const result = await postCoordsToServer(lat, lng);

  if (result === null) {
    // Network/server failure — back off exponentially so a dead
    // server doesn't get hammered by every enrolled user every 30s.
    retryDelay = Math.min(retryDelay * 2, MAX_DELAY);
    return;
  }

  const { status } = result;

  if (status === 'success') {
    retryDelay = BASE_DELAY;
    await cleanupOldFlags(flagCache, today);
    await flagCache.put(doneKey, new Response('1'));
    await showNotification('✅ Attendance Marked!',
      "You're at EnterGYM — attendance logged automatically.");
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(c => c.postMessage({ type: 'ATTENDANCE_MARKED' }));
    stopPolling();

  } else if (status === 'exists') {
    retryDelay = BASE_DELAY;
    await cleanupOldFlags(flagCache, today);
    await flagCache.put(doneKey, new Response('1'));
    stopPolling();

  } else if (status === 'expired' || status === 'not_enrolled') {
    isEnrolled = false;
    stopPolling();

  } else {
    // 'out_of_range', 'rate_limited', etc. — server is alive and responding,
    // so reset backoff and keep polling at normal cadence.
    retryDelay = BASE_DELAY;
  }
}

// ── POST user's coords to server ─────────────────────────────
async function postCoordsToServer(lat, lng) {
  try {
    const res = await fetchWithTimeout('/api/mark-attendance/', {
      method:      'POST',
      credentials: 'include',
      headers:     { 'Content-Type': 'application/json' },
      body:        JSON.stringify({ lat, lng }),
    });

    if (res.status === 429) return { status: 'rate_limited' };
    if (res.status === 403) {
      const data = await res.json().catch(() => ({}));
      return { status: data.status || 'forbidden' };
    }
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
      if (err.name === 'AbortError') {
          console.warn('Attendance request timed out.');
      }

      return null;
    }
}

// ── Push notification ─────────────────────────────────────────
async function showNotification(title, body) {
  if (Notification.permission !== 'granted') return;
  if (self.registration.showNotification) {
    self.registration.showNotification(title, {
      body,
      icon:     '/static/images/Logo.png',
      badge:    '/static/images/Logo.png',
      tag:      'gym-attendance',
      renotify: false,
      data:     { url: '/attendence/' },
    });
  }
}

// ── Notification click → open attendance page ─────────────────
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then(clients => {
      for (const c of clients) {
        if (c.url.includes('/attendence/')) { c.focus(); return; }
      }
      return self.clients.openWindow('/attendence/');
    })
  );
});

// Handle incoming push
self.addEventListener('push', function(event) {
    const data = event.data.json();
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/icons/icon-192.png',
            badge: '/static/icons/icon-72.png',
            data: { url: data.url }
        })
    );
});