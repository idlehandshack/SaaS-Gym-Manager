// ============================================================
//  notification_permission.js
//  Place in: static/js/notification_permission.js
//
//  Renders the notification permission card / turned-off card
//  inside #notif-permission-slot on the profile page.
//
//  Does NOT touch push subscription logic — it only calls
//  window.EnterGYMPush.requestAndSubscribe() (from the
//  unchanged push_subscribe.js) after the user opts in via the
//  explanation modal.
//
//  Rules followed:
//   - Never calls Notification.requestPermission() on page load.
//   - Only requests permission after "Enable Notifications" ->
//     explanation modal -> "Allow Notifications".
//   - Friendly, reassuring, non-alarming copy throughout.
//   - Never re-prompts once denied.
//
//  Load AFTER push_subscribe.js (needs window.EnterGYMPush).
// ============================================================
(function () {
  'use strict';

  const slot = document.getElementById('notif-permission-slot');
  if (!slot) return;

  if (!('Notification' in window)) {
    // Browser doesn't support notifications at all — stay silent.
    return;
  }
  render();

  function render() {    
    const state = Notification.permission;

    if (state === 'granted') {
      slot.innerHTML = '';
      return;
    }
    if (sessionStorage.getItem('notif-dismissed')) {
        slot.innerHTML = '';
        return;
    }
    if (state === 'denied') {
      renderTurnedOffCard();
      return;
    }
    renderEnableCard();
  }

  // ── Card: permission not yet requested (DEFAULT) ────────────
  function renderEnableCard() {
    slot.innerHTML = `
      <div class="notif-card">
        <div class="notif-card-body">
          <h3 class="notif-card-title">🔔 Stay Connected with Your Gym</h3>
          <p class="notif-card-desc">Enable notifications to receive:</p>
          <ul class="notif-card-list notif-card-list--check">
            <li>Get payment receipts, renewal reminders,
                attendance updates and other important
                gym notifications.</li>
          </ul>
          <p class="notif-card-privacy">
            EnterGYM only sends notifications related to your gym membership.
          </p>
          <ul class="notif-card-privacy-list">
            <li>Your privacy matters. EnterGYM only sends gym-related notifications. No ads, no spam, and you can disable notifications anytime.</li>
          </ul>
        </div>
        <div class="notif-card-actions">
          <button type="button" class="notif-btn-enable" id="notif-btn-enable">Enable Notifications</button>
          <button type="button" class="notif-btn-skip" id="notif-btn-skip">Maybe Later</button>
        </div>
      </div>
    `;

    document.getElementById('notif-btn-enable').addEventListener('click', openExplainerModal);
    document.getElementById('notif-btn-skip').addEventListener('click', () => {
      sessionStorage.setItem('notif-dismissed', '1');
          slot.innerHTML = '';
    });
  }

  // ── Card: permission previously blocked (DENIED) — friendly, not scary ──
  function renderTurnedOffCard() {
    slot.innerHTML = `
      <div class="notif-card notif-card-blocked">
        <div class="notif-card-icon">🔕</div>
        <div class="notif-card-body">
          <h3 class="notif-card-title">Notifications are Turned Off</h3>
          <p class="notif-card-desc">
            You can still use EnterGYM normally. Everything continues to work
            including:
          </p>
          <ul class="notif-card-list notif-card-list--check">
            <li>Attendance</li>
            <li>Membership</li>
            <li>Payments</li>
            <li>Profile</li>
          </ul>
          <p class="notif-card-desc" style="margin-top:10px;">
            However, you won't receive:
          </p>
          <ul class="notif-card-list">
            <li>Payment Receipts</li>
            <li>Membership Renewal Reminders</li>
            <li>Attendance Confirmation</li>
            <li>Gym Announcements</li>
          </ul>
        </div>
        <div class="notif-card-actions">
          <button type="button" class="notif-btn-enable" id="notif-btn-help">Show Me How</button>
          <button type="button" class="notif-btn-skip" id="notif-btn-later">Maybe Later</button>
        </div>
      </div>
    `;

    document.getElementById('notif-btn-help').addEventListener('click', openHelpModal);
    document.getElementById('notif-btn-later').addEventListener('click', () => {
      sessionStorage.setItem('notif-dismissed', '1');
        slot.innerHTML = '';
    });
  }

  // ── Explanation modal, shown BEFORE the browser prompt ──────
  function openExplainerModal() {
    const modal = document.createElement('div');
    modal.className = 'notif-modal-backdrop';
    modal.innerHTML = `
      <div class="notif-modal-card">
        <span class="notif-modal-icon">🔔</span>
        <h2 class="notif-modal-title">Stay Updated</h2>
        <p class="notif-modal-body">
          EnterGYM would like permission to send important notifications
          about your membership. You'll receive:
        </p>
        <ul class="notif-help-steps notif-help-steps--check">
          <li>Payment receipts</li>
          <li>Renewal reminders</li>
          <li>Attendance confirmation</li>
          <li>Gym announcements</li>
        </ul>
        <p class="notif-modal-body">
          We never send advertisements. We never spam. We never track you
          in the background.
        </p>
        <div class="notif-modal-actions">
          <button type="button" class="notif-btn-enable" id="notif-modal-continue">Allow Notifications</button>
          <button type="button" class="notif-btn-skip" id="notif-modal-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
    document.getElementById('notif-modal-cancel').addEventListener('click', closeModal);
    document.getElementById('notif-modal-continue').addEventListener('click', async () => {
      const btn = document.getElementById('notif-modal-continue');
      btn.disabled = true;
      btn.textContent = 'Requesting…';

      // Unchanged push subscription architecture — just call the
      // existing, untouched entry point from push_subscribe.js.
      const api = window.EnterGYMPush;
      const result = api
        ? await api.requestAndSubscribe()
        : { status: await Notification.requestPermission() };

      closeModal();

      if (result && result.status === 'granted') {
        sessionStorage.removeItem('notif-dismissed');
        slot.innerHTML = '';
        showSuccessToast();
      } else {
        render(); // re-render card/turned-off state based on new permission
      }
    });

    function closeModal() {
      modal.remove();
    }
  }

  // ── "Show Me How" modal: friendly, browser-agnostic steps ───
  function openHelpModal() {
    const modal = document.createElement('div');
    modal.className = 'notif-modal-backdrop';
    modal.innerHTML = `
      <div class="notif-modal-card">
        <span class="notif-modal-icon">⚙️</span>
        <h2 class="notif-modal-title">Enable Notifications</h2>
        <p class="notif-modal-body">
          Notifications were previously blocked in your browser. To enable
          them again:
        </p>
        <ol class="notif-help-steps">
          <li>Open your browser's Site Settings.</li>
          <li>Find Notifications.</li>
          <li>Change permission to Allow.</li>
          <li>Refresh this page.</li>
        </ol>
        <p class="notif-modal-footnote">
          These steps may look slightly different depending on whether
          you're using Chrome, Edge, Firefox, Samsung Internet, or Safari.
        </p>
        <div class="notif-modal-actions">
          <button type="button" class="notif-btn-enable" id="notif-help-close">Got It</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.getElementById('notif-help-close').addEventListener('click', () => modal.remove());
  }

  // ── Success toast ────────────────────────────────────────────
  function showSuccessToast() {
    const toast = document.createElement('div');
    toast.className = 'notif-success-toast';
    toast.textContent = '✅ Notifications enabled successfully.';
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('notif-toast-hide');
      setTimeout(() => toast.remove(), 400);
    }, 3200);
  }

})();