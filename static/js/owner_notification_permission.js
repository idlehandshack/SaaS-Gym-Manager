document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    const buttons = document.querySelectorAll(".owner-notif-btn");
    if (buttons.length === 0) {
        console.warn("No notification buttons found");
        return;
    }
    buttons.forEach(btn => {
        btn.addEventListener("click", onNotificationClick);
    });

    async function onNotificationClick(e) {
        e.preventDefault();
        e.stopPropagation();
        const state = Notification.permission;

        if (state === "granted") {
            showInfoModal(
                "🔔",
                "Notifications Already Enabled",
                "You're all set."
            );
            return;
        }

        if (state === "denied") {
            openHelpModal();
            return;
        }
        openExplainerModal();
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
          about your gym. You'll receive:
        </p>
        <ul class="notif-help-steps notif-help-steps--check">
          <li>Payment alerts</li>
          <li>Membership renewals</li>
          <li>Attendance updates</li>
          <li>Support requests</li>
        </ul>
        <p class="notif-modal-body">
          We never send advertisements. We never spam. We never track you
          in the background.
        </p>
        <div class="notif-modal-actions">
          <button type="button" class="notif-btn-enable" id="owner-notif-modal-continue">Allow Notifications</button>
          <button type="button" class="notif-btn-skip" id="owner-notif-modal-cancel">Cancel</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
    document.getElementById('owner-notif-modal-cancel').addEventListener('click', closeModal);
    document.getElementById('owner-notif-modal-continue').addEventListener('click', async () => {
      const continueBtn = document.getElementById('owner-notif-modal-continue');
      continueBtn.disabled = true;
      continueBtn.textContent = 'Requesting…';

      // Unchanged push subscription architecture — just call the
      // existing, untouched entry point from push_subscribe.js.
      const api = window.EnterGYMPush;
      const result = api
        ? await api.requestAndSubscribe()
        : { status: await Notification.requestPermission() };

      closeModal();

      if (result && result.status === 'granted') {
        showSuccessToast();
      } else if (result && result.status === 'denied') {
        openHelpModal();
      }
      // If dismissed (neither granted nor denied), do nothing —
      // owner can click the button again anytime.
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
          <button type="button" class="notif-btn-enable" id="owner-notif-help-close">Got It</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.getElementById('owner-notif-help-close').addEventListener('click', () => modal.remove());
  }

  // ── Small info modal (e.g. "already enabled") ───────────────
  function showInfoModal(icon, title, body) {
    const modal = document.createElement('div');
    modal.className = 'notif-modal-backdrop';
    modal.innerHTML = `
      <div class="notif-modal-card">
        <span class="notif-modal-icon">${icon}</span>
        <h2 class="notif-modal-title">${title}</h2>
        <p class="notif-modal-body">${body}</p>
        <div class="notif-modal-actions">
          <button type="button" class="notif-btn-enable" id="owner-notif-info-close">Got It</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    document.getElementById('owner-notif-info-close').addEventListener('click', () => modal.remove());
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

});