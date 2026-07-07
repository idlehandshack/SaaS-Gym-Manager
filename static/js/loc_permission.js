// ============================================================
//  loc_permission.js
//  Place in: static/js/loc_permission.js
//
//  REDESIGNED BEHAVIOR (per "Location Permission" spec)
//  ------------------------------------------------------------
//  - NEVER requests location on page load.
//  - NEVER calls navigator.geolocation.getCurrentPosition() until
//    the user explicitly taps "Mark Attendance" AND (if needed)
//    confirms the explanation modal.
//  - Exposes window.EnterGYMLocation.checkLocationAndSubmit(onReady)
//    which geo_attendance.js calls from the Mark Attendance button.
//  - Detects granted / prompt / denied via the Permissions API and
//    reacts to permission changes live (no reload needed).
//
//  Load this in base.html / attendance page BEFORE geo_attendance.js
// ============================================================

(function () {
  'use strict';

  const cfg = window.GYM_CONFIG || {};

  let modalEl        = null;
  let blockedCardEl   = null;
  let permissionStatus = null; // PermissionStatus object, if supported

  // ── Public entry point ──────────────────────────────────────
  // Called when the user taps "Mark Attendance".
  // onSuccess(position) is invoked once we actually have a GPS fix.
  // onBlocked() is invoked if permission is denied (UI already shown here).
  function checkLocationAndSubmit(onSuccess, onBlocked) {
    if (!navigator.geolocation) {
      showUnsupportedError();
      return;
    }

    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: 'geolocation' }).then(status => {
        attachChangeListener(status);
        handlePermissionState(status.state, onSuccess, onBlocked);
      }).catch(() => {
        // Permissions API failed — fall back to explanation modal first
        showExplanationModal(onSuccess, onBlocked);
      });
    } else {
      // No Permissions API (older Safari) — always show explanation modal
      // first; getCurrentPosition is only called after user confirms.
      showExplanationModal(onSuccess, onBlocked);
    }
  }
  // ── Read-only permission check (no GPS, no modal) ────────────
  // Used by geo_attendance.js on page load to decide whether it's
  // safe to auto-start background tracking. Never requests GPS and
  // never shows any UI — just reports the current browser state.
  function getPermissionState(callback) {
    if (!navigator.geolocation) {
      callback('unsupported');
      return;
    }
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: 'geolocation' })
        .then(status => callback(status.state))   // 'granted' | 'denied' | 'prompt'
        .catch(() => callback('unknown'));
    } else {
      // No Permissions API (older Safari) — we cannot know without
      // prompting, and prompting is not allowed here, so treat as
      // unknown/prompt-like: caller should NOT auto-start.
      callback('unknown');
    }
  }

  function handlePermissionState(state, onSuccess, onBlocked) {
    if (state === 'granted') {
      // No modal — go straight to GPS, no extra click for the user.
      requestPosition(onSuccess, onBlocked);
    } else if (state === 'denied') {
      showBlockedCard(onBlocked);
    } else {
      // 'prompt' — explain before the browser dialog appears.
      showExplanationModal(onSuccess, onBlocked);
    }
  }

  // ── Live permission-change listener ─────────────────────────
  function attachChangeListener(status) {
    if (!status || permissionStatus === status) return;
    permissionStatus = status;
    status.onchange = function () {
      if (status.state === 'granted') {
        removeBlockedCard();
        removeModal();
      } else if (status.state === 'denied') {
        removeModal();
      }
    };
  }

  // ── Explanation modal (shown BEFORE first browser prompt) ───
  function showExplanationModal(onSuccess, onBlocked) {
    removeModal();

    modalEl = document.createElement('div');
    modalEl.id = 'loc-explain-modal';
    modalEl.setAttribute('role', 'dialog');
    modalEl.setAttribute('aria-modal', 'true');
    modalEl.setAttribute('aria-labelledby', 'loc-explain-title');
    modalEl.innerHTML = `
      <div class="loc-modal-backdrop"></div>
      <div class="loc-modal-card" tabindex="-1">
        <span class="loc-modal-icon" aria-hidden="true">📍</span>
        <h2 class="loc-modal-title" id="loc-explain-title">Location Required</h2>
        <p class="loc-modal-body">
          To prevent fake attendance and ensure you are physically present
          inside your gym, EnterGYM needs temporary access to your current
          location when you mark attendance.
        </p>
        <p class="loc-modal-body loc-modal-subtle">
          Your location is only checked at the moment you tap
          <strong>Mark Attendance</strong>. It is never continuously monitored.
        </p>

        <div class="loc-privacy-box">
          <h3 class="loc-privacy-title">🔒 Your Privacy Matters</h3>
          <ul class="loc-privacy-list">
            <li>We do <strong>not</strong> continuously track your location</li>
            <li>We do <strong>not</strong> store your live location history</li>
            <li>We do <strong>not</strong> sell or share your location</li>
            <li>We do <strong>not</strong> access location while you browse</li>
          </ul>
          <p class="loc-privacy-footer">
            Location is requested only when you choose to mark attendance.
            You can change this anytime from your browser settings.
          </p>
        </div>

        <div class="loc-modal-actions">
          <button class="btn-loc-continue" id="btn-loc-continue">Continue</button>
          <button class="btn-loc-cancel" id="btn-loc-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(modalEl);
    document.body.style.overflow = 'hidden';

    const card = modalEl.querySelector('.loc-modal-card');
    card.focus();
    trapFocus(modalEl, card);

    document.getElementById('btn-loc-continue').addEventListener('click', () => {
      const btn = document.getElementById('btn-loc-continue');
      btn.disabled = true;
      btn.textContent = 'Requesting…';
      // First and only place we call getCurrentPosition after "prompt".
      requestPosition(
        (pos) => { removeModal(); if (onSuccess) onSuccess(pos); },
        (err) => { removeModal(); if (onBlocked) onBlocked(err); }
      );
    });

    document.getElementById('btn-loc-cancel').addEventListener('click', () => {
      removeModal();
    });

    modalEl.querySelector('.loc-modal-backdrop').addEventListener('click', removeModal);

    function onKeydown(e) {
      if (e.key === 'Escape') removeModal();
    }
    modalEl.addEventListener('keydown', onKeydown);
  }

  function removeModal() {
    if (modalEl) {
      modalEl.remove();
      modalEl = null;
    }
    document.body.style.overflow = '';
  }

  // ── Blocked / denied permission card ────────────────────────
  function showBlockedCard(onBlocked) {
    removeBlockedCard();

    blockedCardEl = document.createElement('div');
    blockedCardEl.id = 'loc-blocked-card';
    blockedCardEl.setAttribute('role', 'alertdialog');
    blockedCardEl.setAttribute('aria-modal', 'true');
    blockedCardEl.innerHTML = `
      <div class="loc-modal-backdrop"></div>
      <div class="loc-modal-card" tabindex="-1">
        <span class="loc-modal-icon" aria-hidden="true">🚫</span>
        <h2 class="loc-modal-title">Location Permission Disabled</h2>
        <p class="loc-modal-body">
          Location permission is currently disabled. EnterGYM requires your
          location only while marking attendance, to verify that you are
          physically inside your gym.
        </p>
        <p class="loc-modal-body loc-modal-subtle">
          Attendance cannot be recorded until Location permission is enabled.
        </p>
        <div class="loc-modal-actions">
          <button class="btn-loc-continue" id="btn-loc-how">How to Enable</button>
          <button class="btn-loc-cancel" id="btn-loc-blocked-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(blockedCardEl);
    document.body.style.overflow = 'hidden';

    const card = blockedCardEl.querySelector('.loc-modal-card');
    card.focus();
    trapFocus(blockedCardEl, card);

    document.getElementById('btn-loc-how').addEventListener('click', showHowToEnable);
    document.getElementById('btn-loc-blocked-cancel').addEventListener('click', removeBlockedCard);
    blockedCardEl.querySelector('.loc-modal-backdrop').addEventListener('click', removeBlockedCard);

    blockedCardEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') removeBlockedCard();
    });

    if (onBlocked) onBlocked();
  }

  function removeBlockedCard() {
    if (blockedCardEl) {
      blockedCardEl.remove();
      blockedCardEl = null;
    }
    document.body.style.overflow = '';
  }

  // ── "How to Enable" instructions modal ──────────────────────
  function showHowToEnable() {
    removeBlockedCard();
    removeModal();

    modalEl = document.createElement('div');
    modalEl.id = 'loc-howto-modal';
    modalEl.setAttribute('role', 'dialog');
    modalEl.setAttribute('aria-modal', 'true');
    modalEl.innerHTML = `
      <div class="loc-modal-backdrop"></div>
      <div class="loc-modal-card" tabindex="-1">
        <span class="loc-modal-icon" aria-hidden="true">📍</span>
        <h2 class="loc-modal-title">Enable Location Access</h2>

        <div class="loc-howto-section">
          <h3>Desktop Browsers</h3>
          <ol>
            <li>Click the lock icon beside the website address.</li>
            <li>Open Site Settings.</li>
            <li>Change Location permission to Allow.</li>
            <li>Refresh the page.</li>
            <li>Try marking attendance again.</li>
          </ol>
        </div>

        <div class="loc-howto-section">
          <h3>Android Chrome</h3>
          <ol>
            <li>Tap the lock icon or browser menu.</li>
            <li>Open Site Settings.</li>
            <li>Tap Permissions.</li>
            <li>Enable Location.</li>
            <li>Return to EnterGYM and mark attendance again.</li>
          </ol>
        </div>

        <div class="loc-howto-section">
          <h3>Installed App (PWA)</h3>
          <p>
            Enable Location from either your browser's Site Settings or your
            device's Operating System App Permissions, depending on your device.
          </p>
        </div>

        <div class="loc-modal-actions">
          <button class="btn-loc-cancel" id="btn-loc-howto-close">Close</button>
        </div>
      </div>
    `;
    document.body.appendChild(modalEl);

    const card = modalEl.querySelector('.loc-modal-card');
    card.focus();
    trapFocus(modalEl, card);

    document.getElementById('btn-loc-howto-close').addEventListener('click', removeModal);
    modalEl.querySelector('.loc-modal-backdrop').addEventListener('click', removeModal);
    modalEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') removeModal();
    });
  }

  // ── Actual GPS request (only ever called after grant/continue) ─
  function requestPosition(onSuccess, onError) {
    navigator.geolocation.getCurrentPosition(
      (pos) => { if (onSuccess) onSuccess(pos); },
      (err) => { handleGeoError(err, onError); },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  }

  function handleGeoError(err, onError) {
    if (err.code === err.PERMISSION_DENIED) {
      showBlockedCard();
    } else if (err.code === err.POSITION_UNAVAILABLE) {
      showInlineError('Unable to determine your current location. Please ensure GPS is enabled and try again.');
    } else if (err.code === err.TIMEOUT) {
      showInlineError('Location request timed out. Please move to an area with better GPS signal and try again.');
    } else {
      showInlineError('An unexpected error occurred while retrieving your location. Please try again.');
    }
    if (onError) onError(err);
  }

  function showUnsupportedError() {
    showInlineError('Your browser does not support location services required for attendance.');
  }

  function showInlineError(text) {
    const errEl = document.getElementById('geo-error');
    const txtEl = document.getElementById('geo-error-text');
    if (errEl && txtEl) {
      txtEl.textContent = '⊘ ' + text;
      errEl.style.display = 'block';
    }
  }

  // ── Simple focus trap for modals ────────────────────────────
  function trapFocus(container, initialFocusEl) {
    const focusable = container.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last  = focusable[focusable.length - 1];

    container.addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }
  // NOTE: nothing runs automatically on load. No permission is ever
  // requested until checkLocationAndSubmit() is called from a user
  // click (see geo_attendance.js's Mark Attendance handler).
  // ── Start background tracking once permission is granted ───
  function startBackgroundTracking(userHash) {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) return;
    navigator.serviceWorker.controller.postMessage({
      type: 'START_GEO',
      config: { isEnrolled: true, userHash: userHash || '' },
    });
  }
  // ── Expose public API ────────────────────────────────────────
  window.EnterGYMLocation = {
    checkLocationAndSubmit: checkLocationAndSubmit,
    getFreshPosition: requestPosition,
    getPermissionState: getPermissionState,
  };
})();