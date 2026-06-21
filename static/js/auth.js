/* ════════════════════════════════════════════════════════════
   AUTH.JS — shared behavior for Login & Signup
   Password visibility toggle + optional strength meter.
   Does not touch form submission, validation, or field names.
   ════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  const EYE_ON = `
    <svg class="icon-on" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
      <circle cx="12" cy="12" r="3"></circle>
    </svg>`;
  const EYE_OFF = `
    <svg class="icon-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a18.5 18.5 0 0 1 4.22-5.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
      <line x1="1" y1="1" x2="23" y2="23"></line>
    </svg>`;

  /* ── Wire a visibility-toggle button onto every password field ── */
  function addPasswordToggles() {
    document.querySelectorAll('input[type="password"]').forEach(function (input) {
      const wrap = input.closest('.form-group');
      if (!wrap || wrap.dataset.toggleWired) return;
      wrap.dataset.toggleWired = "1";
      wrap.classList.add('has-toggle');

      // Build an input-wrap if one doesn't already exist
      let inputWrap = input.parentElement;
      if (!inputWrap.classList.contains('input-wrap')) {
        inputWrap = document.createElement('div');
        inputWrap.className = 'input-wrap';
        input.parentNode.insertBefore(inputWrap, input);
        inputWrap.appendChild(input);
      }

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pw-toggle';
      btn.setAttribute('aria-label', 'Show password');
      btn.innerHTML = EYE_ON + EYE_OFF;
      inputWrap.appendChild(btn);

      btn.addEventListener('click', function () {
        const showing = input.type === 'text';
        input.type = showing ? 'password' : 'text';
        btn.classList.toggle('is-visible', !showing);
        btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
      });
    });
  }

  /* ── Optional password-strength meter, only attaches if a
     [data-strength-for] hook exists pointing at a password field ── */
  function addStrengthMeter() {
    const target = document.querySelector('[data-strength-for]');
    if (!target) return;
    const input = document.querySelector(target.dataset.strengthFor);
    if (!input) return;

    const meter = document.createElement('div');
    meter.className = 'pw-strength';
    meter.setAttribute('data-level', '0');
    meter.innerHTML = '<div class="pw-strength-bar"></div><div class="pw-strength-bar"></div><div class="pw-strength-bar"></div>';

    const label = document.createElement('div');
    label.className = 'pw-strength-label';
    label.textContent = '';

    target.appendChild(meter);
    target.appendChild(label);

    function score(value) {
      let s = 0;
      if (value.length >= 8) s++;
      if (/[0-9]/.test(value) && /[a-zA-Z]/.test(value)) s++;
      if (value.length >= 8 && /[0-9]/.test(value) && /[a-zA-Z]/.test(value) && /[^a-zA-Z0-9]/.test(value)) s++;
      return Math.min(s, 3);
    }

    const LABELS = ['', 'Weak', 'Okay', 'Strong'];

    input.addEventListener('input', function () {
      const val = input.value;
      if (!val) {
        meter.setAttribute('data-level', '0');
        label.textContent = '';
        return;
      }
      const lvl = Math.max(1, score(val));
      meter.setAttribute('data-level', String(lvl));
      label.textContent = LABELS[lvl];
    });
  }

  /* ── Flash message close buttons (in case messages.js isn't loaded on this page) ── */
  function wireFlashClose() {
    document.querySelectorAll('#message-container .close-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const msg = btn.closest('.flash-message');
        if (msg) msg.remove();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    addPasswordToggles();
    addStrengthMeter();
    wireFlashClose();
  });
})();