// ============================================================
//  geo_attendance.js
//
//  Owns:
//   - calling /api/mark-attendance/
//   - attendance UI (loading, success, errors)
//   - deciding whether background tracking should start, by
//     checking /api/attendance-status/ AFTER a successful mark
//   - wiring the Service Worker messages related to attendance
//     (REQUEST_LOC → get a fix via loc_permission.js; ATTENDANCE_MARKED)
//
//  Never calls navigator.geolocation directly — always goes through
//  window.EnterGYMLocation.
// ============================================================

(function () {
  'use strict';

  function getCookie(name) {
    return document.cookie
      .split(';')
      .map(c => c.trim())
      .find(c => c.startsWith(name + '='))
      ?.split('=')[1] ?? '';
  }

  // ── Entry point called from the Mark Attendance button ──────
  function checkLocationAndSubmit() {
    const btn = document.getElementById('btn-attend');
    if (!btn || btn.disabled) return;

    if (!window.EnterGYMLocation) {
      console.error('EnterGYM: loc_permission.js not loaded');
      showGeoError('LOCATION MODULE UNAVAILABLE — please reload the page.');
      return;
    }

    hideGeoError();

    window.EnterGYMLocation.checkLocationAndSubmit(
      function onSuccess(position) {
        submitAttendance(
          position.coords.latitude,
          position.coords.longitude,
          btn
        );
      },
      function onBlocked() {
        // loc_permission.js already renders the blocked card / modal.
      }
    );
  }
  // ── POST coordinates to Django ───────────────────────────────
  async function submitAttendance(lat, lng, btn) {
    setButtonLoading(btn, true);

    try {
      const res = await EnterGYMFetch.fetchWithTimeout(
          '/api/mark-attendance/',
          {
              method: 'POST',
              headers: {
                  'Content-Type': 'application/json',
                  'X-CSRFToken': getCookie('csrftoken')
              },
              credentials: 'include',
              body: JSON.stringify({lat, lng})
          }
      );

      let data = {};
      try { data = await res.json(); } catch (_) { /* non-JSON response */ }

      if (res.ok && data.status === 'success') {
        hideGeoError();
        markAttendanceSuccess(btn);
        maybeStartBackgroundTracking();   // ← decision point 1: success
        return;
      }

      if (data.status === 'exists') {
        hideGeoError();
        markAttendanceSuccess(btn);
        maybeStartBackgroundTracking();   // ← decision point 2: exists
        return;
      }

      if (data.status === 'out_of_range') {
        showGeoError('OUT OF RANGE — you must be inside the gym to mark attendance', data.distance_message);
        setButtonLoading(btn, false);
        return;
      }

      if (res.status === 429) {
        showGeoError('TOO MANY ATTEMPTS — please wait a moment and try again');
        setButtonLoading(btn, false);
        return;
      }

      if (res.status === 403) {
        showGeoError(data.message || 'NOT AUTHORIZED — please contact gym staff');
        setButtonLoading(btn, false);
        return;
      }

      // expired / not_enrolled / rate_limited / error — no tracking start
      showGeoError('ATTENDANCE FAILED — please try again');
      setButtonLoading(btn, false);

    } catch (err) {
      showGeoError('NETWORK ERROR — please check your connection and try again');
      setButtonLoading(btn, false);
    }
  }

  // ── Background tracking gate ─────────────────────────────────
  // Called ONLY after the attendance request has already completed
  // (success or exists), so there is no race with START_GEO firing
  // before the manual POST lands.
  async function maybeStartBackgroundTracking() {
    try {
      const res = await EnterGYMFetch.fetchWithTimeout('/api/attendance-status/', {
        method: 'GET',
        credentials: 'include',
      });
      if (!res.ok) return;

      const { enrolled, marked } = await res.json();

      if (enrolled === true && marked === false) {
        startBackgroundTracking();
      }
      // enrolled === false → do nothing
      // marked === true    → do nothing (nothing left to auto-mark)
    } catch (_) {
      // Network hiccup — silently skip; the user already has attendance
      // marked (or existing) either way, so this is non-critical.
    }
  }

  function startBackgroundTracking() {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
    const cfg = window.GYM_CONFIG || {};
    navigator.serviceWorker.controller.postMessage({
      type: 'START_GEO',
      config: { isEnrolled: true, userHash: cfg.userHash || '' },
    });
  }

  // ── UI helpers ────────────────────────────────────────────────
  function setButtonLoading(btn, isLoading) {
    if (!btn) return;
    btn.disabled = isLoading;
    btn.classList.toggle('loading', isLoading);
  }
  function initAutomaticAttendance() {
    const btn = document.getElementById('btn-attend');
    if (btn && btn.disabled) return;          // already marked today server-side

    if (!window.EnterGYMLocation) return;

    window.EnterGYMLocation.getPermissionState((state) => {
      if (state !== 'granted') return;         // 'prompt' / 'denied' / 'unknown' → no-op
      maybeStartBackgroundTracking();
    });
  }
  function markAttendanceSuccess(btn) {
    if (!btn) return;
    btn.classList.remove('loading');
    btn.classList.add('marked');
    btn.disabled = true;
    const label = document.getElementById('btn-label');
    if (label) {
      label.innerHTML = '<span class="check-icon">✓</span> ATTENDANCE LOGGED';
    }
    const banner = document.getElementById('auto-mark-banner');
    if (banner) banner.style.display = 'none';
  }

  function showGeoError(text, distance) {
    const errEl  = document.getElementById('geo-error');
    const txtEl  = document.getElementById('geo-error-text');
    const distEl = document.getElementById('geo-distance');
    if (errEl && txtEl) {
      txtEl.textContent = '⊘ ' + text;
      errEl.style.display = 'block';
      if (distEl) distEl.textContent = distance || '';
    }
  }

  function hideGeoError() {
    const errEl = document.getElementById('geo-error');
    if (errEl) errEl.style.display = 'none';
  }

  // ── Service Worker message wiring (attendance-related only) ───
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', function (event) {
      const msg = event.data || {};

      if (msg.type === 'ATTENDANCE_MARKED') {
        const btn = document.getElementById('btn-attend');
        markAttendanceSuccess(btn);
        return;
      }

      // SW wants a fresh GPS fix for its 30s poll. GPS retrieval itself
      // is loc_permission.js's job — we just relay the result back.
      if (msg.type === 'REQUEST_LOC') {
        if (!window.EnterGYMLocation) return;
        window.EnterGYMLocation.getFreshPosition(
          (pos) => {
            navigator.serviceWorker.controller?.postMessage({
              type: 'REPORT_LOC',
              lat: pos.coords.latitude,
              lng: pos.coords.longitude,
            });
          },
          () => { /* silent — background fix failed, SW retries in 30s */ }
        );
      }
    });
  }
  // ── Wire up the button + auto-start on load ───────────────────
  document.addEventListener('DOMContentLoaded', function () {
    const btn = document.getElementById('btn-attend');
    if (btn && !btn.disabled) {
      btn.removeEventListener('click', checkLocationAndSubmit);
      btn.addEventListener('click', checkLocationAndSubmit);
    }
    initAutomaticAttendance();
  });

  window.checkLocationAndSubmit = checkLocationAndSubmit;

})();