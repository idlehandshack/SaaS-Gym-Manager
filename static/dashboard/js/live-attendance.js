/**
 * Real-time attendance notifications over WebSocket.
 * Connects once per dashboard page load (base.html), auto-reconnects,
 * renders stacked toasts, plays a sound, and patches dashboard counters
 * without a page refresh.
 *
 * Expects two globals set by base.html before this script loads:
 *   window.LAC_GYM_ID   — integer gym id, or null/undefined to skip entirely
 *   window.LAC_WS_URL    — full ws(s):// URL to connect to
 */
(function () {
  'use strict';

  if (!window.LAC_GYM_ID || !window.LAC_WS_URL) return;

  const MAX_VISIBLE = 5;
  const AUTO_HIDE_MS = 6000;
  const RECONNECT_BASE_MS = 1000;
  const RECONNECT_MAX_MS = 30000;
  const BURST_WINDOW_MS = 4000;
  const BURST_THRESHOLD = 6;
  let burstTimestamps = [];
  let burstSummaryToast = null;
  let burstCount = 0;

  let socket = null;
  let reconnectDelay = RECONNECT_BASE_MS;
  let reconnectTimer = null;
  let intentionalClose = false;
  let soundUnlocked = false;

  const container = document.getElementById('lacContainer');
  const soundEl = document.getElementById('lacSound');

  // Browsers block autoplay audio until a user gesture happens on the page.
  function unlockSoundOnce() {
    if (soundUnlocked) return;
    soundUnlocked = true;
    document.removeEventListener('click', unlockSoundOnce);
    document.removeEventListener('keydown', unlockSoundOnce);
  }
  document.addEventListener('click', unlockSoundOnce, { once: true });
  document.addEventListener('keydown', unlockSoundOnce, { once: true });

  function connect() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return; // prevent duplicate connections
    }
    intentionalClose = false;
    socket = new WebSocket(window.LAC_WS_URL);

    socket.addEventListener('open', function () {
      reconnectDelay = RECONNECT_BASE_MS; // reset backoff on success
    });

    socket.addEventListener('message', function (event) {
      try {
        const payload = JSON.parse(event.data);
        handleAttendancePayload(payload);
      } catch (err) {
        console.error('live-attendance: bad message payload', err);
      }
    });

    socket.addEventListener('close', function () {
      if (!intentionalClose) scheduleReconnect();
    });

    socket.addEventListener('error', function () {
      socket.close();
    });
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
      connect();
    }, reconnectDelay);
  }

  window.addEventListener('beforeunload', function () {
    intentionalClose = true;
    if (socket) socket.close();
  });

  // ── Rendering ─────────────────────────────────────────────────────────
  function handleAttendancePayload(p) {
    if (p.type === 'register_scan_completed') {
      renderRegisterScanSummaryToast(p);
      playSound();
      patchDashboardCounters(p);
      return;
    }

    const now = Date.now();
    burstTimestamps.push(now);
    burstTimestamps = burstTimestamps.filter(t => now - t < BURST_WINDOW_MS);

    if (burstTimestamps.length > BURST_THRESHOLD) {
      collapseIntoSummary(p);
    } else {
      renderToast(p);
      playSound();
    }
    patchDashboardCounters(p);
  }

  function collapseIntoSummary(p) {
    burstCount++;
    if (!burstSummaryToast || !burstSummaryToast.parentNode) {
      Array.from(container.children).forEach(removeToast);

      burstSummaryToast = document.createElement('div');
      burstSummaryToast.className = 'lac-toast lac-toast-summary';
      burstSummaryToast.innerHTML = `
        <div class="lac-toast-header">
          <i class="bi bi-lightning-charge-fill"></i>
          <span class="lac-toast-title">Bulk Attendance Update</span>
          <button class="lac-toast-close" aria-label="Dismiss">&times;</button>
        </div>
        <div class="lac-toast-body">
          <div class="lac-toast-info">
            <div class="lac-toast-name" id="lacBurstCount">${burstCount} check-ins marked</div>
            <div class="lac-toast-meta">Updating live…</div>
          </div>
        </div>
        <div class="lac-toast-footer">View Attendance →</div>
      `;
      burstSummaryToast.addEventListener('click', function (e) {
        if (e.target.closest('.lac-toast-close')) { removeToast(burstSummaryToast); return; }
        window.location.href = '/admin-tools/today-attendance/';
      });
      container.appendChild(burstSummaryToast);
      requestAnimationFrame(function () { burstSummaryToast.classList.add('lac-in'); });
    } else {
      const countEl = burstSummaryToast.querySelector('#lacBurstCount');
      if (countEl) countEl.textContent = `${burstCount} check-ins marked`;
    }

    clearTimeout(burstSummaryToast._hideTimer);
    burstSummaryToast._hideTimer = setTimeout(function () {
      removeToast(burstSummaryToast);
      burstSummaryToast = null;
      burstCount = 0;
      burstTimestamps = [];
    }, AUTO_HIDE_MS);
  }
  function avatarFallback(gender) {
    if (gender === 'M') return '/static/dashboard/images/avatar-male.png';
    if (gender === 'F') return '/static/dashboard/images/avatar-female.png';
    return '/static/dashboard/images/avatar-placeholder.png';
  }
  function renderRegisterScanSummaryToast(p) {
    const toast = document.createElement('div');
    toast.className = 'lac-toast lac-toast-register-scan';
    toast.innerHTML = `
      <div class="lac-toast-header">
        <i class="bi bi-camera-fill"></i>
        <span class="lac-toast-title">Register Import Completed</span>
        <button class="lac-toast-close" aria-label="Dismiss">&times;</button>
      </div>
      <div class="lac-toast-body">
        <div class="lac-toast-info">
          <div class="lac-toast-name">Processed: ${p.total}</div>
          <div class="lac-toast-meta">Imported: ${p.imported} · Already Present: ${p.duplicates} · Failed: ${p.failed}</div>
          <div class="lac-toast-meta">${escapeHtml(p.time || '')}</div>
        </div>
      </div>
      <div class="lac-toast-footer">View Attendance →</div>
    `;

    toast.addEventListener('click', function (e) {
      if (e.target.closest('.lac-toast-close')) { removeToast(toast); return; }
      window.location.href = '/admin-tools/today-attendance/';
    });

    container.appendChild(toast);
    requestAnimationFrame(function () { toast.classList.add('lac-in'); });

    while (container.children.length > MAX_VISIBLE) {
      removeToast(container.firstElementChild);
    }

    setTimeout(function () { removeToast(toast); }, AUTO_HIDE_MS);
  }
  function renderToast(p) {
  const toast = document.createElement('div');
  toast.className = 'lac-toast';
  toast.innerHTML = `
    <div class="lac-toast-header">
      <i class="bi bi-check-circle-fill"></i>
      <span class="lac-toast-title">Attendance Marked</span>
      <button class="lac-toast-close" aria-label="Dismiss">&times;</button>
    </div>
    <div class="lac-toast-body">
      <img class="lac-toast-photo" src="${p.photo || avatarFallback(p.gender)}" alt="">
      <div class="lac-toast-info">
        <div class="lac-toast-name">${escapeHtml(p.name)}</div>
        <div class="lac-toast-meta">${escapeHtml(p.phone)} · ${escapeHtml(p.plan)}</div>
        <div class="lac-toast-meta">${escapeHtml(p.attendance_time)} · ${escapeHtml(p.payment_status)}</div>
        <div class="lac-toast-badges">
          <span class="lac-badge lac-badge-${p.method_color}">${escapeHtml(p.method_label)}</span>
          <span class="lac-badge lac-badge-${p.days_remaining_color}">${escapeHtml(p.days_remaining_label)}</span>
        </div>
      </div>
    </div>
    <div class="lac-toast-footer">View Attendance →</div>
  `;

    toast.addEventListener('click', function (e) {
      if (e.target.closest('.lac-toast-close')) {
        removeToast(toast);
        return;
      }
      window.location.href = `/admin-tools/today-attendance/?highlight=${encodeURIComponent(p.attendance_id)}`;
    });

    container.appendChild(toast);
    requestAnimationFrame(function () { toast.classList.add('lac-in'); });

    // Cap visible toasts
    while (container.children.length > MAX_VISIBLE) {
      removeToast(container.firstElementChild);
    }

    setTimeout(function () { removeToast(toast); }, AUTO_HIDE_MS);
  }

  function removeToast(toast) {
    if (!toast || toast.classList.contains('lac-out')) return;
    toast.classList.add('lac-out');
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  function playSound() {
    if (!soundEl) return;
    try {
      soundEl.currentTime = 0;
      soundEl.volume = 0.7;
      soundEl.play().catch(function () { /* autoplay blocked — fine, visual toast still shows */ });
    } catch (e) { /* ignore */ }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  // ── Live dashboard patching (no page refresh) ───────────────────────
  // Dispatches a CustomEvent other page scripts (dashboard.js, stat-cards.js)
  // can listen for, so each page decides how to update its own widgets
  // instead of this file reaching into DOM it doesn't own.
  function patchDashboardCounters(p) {
    document.dispatchEvent(new CustomEvent('lac:attendance-marked', { detail: p }));

    const counterEl = document.querySelector('[data-lac-today-count]');
    if (!counterEl) return;

    if (p.type === 'register_scan_completed') {
      // Authoritative value from the server — never derived locally, so a
      // batch import can't drift the counter out of sync with the DB.
      counterEl.textContent = p.attendance_today;
    } else {
      const current = parseInt(counterEl.textContent.replace(/\D/g, ''), 10) || 0;
      counterEl.textContent = current + 1;
    }
  }

  connect();
})();
