// ============================================================
//  today_attendance.js
//  CSP-safe: only addEventListener, no inline handlers.
// ============================================================
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {

    // ── Tabs (Morning / Evening) ─────────────────────────────
    var tabButtons = document.querySelectorAll('.tab-btn');
    var panels = document.querySelectorAll('.tab-panel');

    function activateTab(tabName) {
      tabButtons.forEach(function (btn) {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
      });
      panels.forEach(function (panel) {
        panel.classList.toggle('active', panel.getAttribute('data-panel') === tabName);
      });
    }

    tabButtons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        activateTab(btn.getAttribute('data-tab'));
      });
    });

    // Fill stat-strip morning/evening counts from panel data-count
    panels.forEach(function (panel) {
      var tab = panel.getAttribute('data-panel');
      var count = panel.getAttribute('data-count') || '0';
      var target = document.getElementById('stat-' + tab);
      if (target) target.textContent = count;
    });

    // ── Alerts collapse/expand ───────────────────────────────
    var alertsToggle = document.getElementById('alerts-toggle');
    var alertsList = document.getElementById('alerts-list');
    if (alertsToggle && alertsList) {
      alertsToggle.addEventListener('click', function () {
        alertsList.classList.toggle('open');
        alertsToggle.classList.toggle('open');
      });
    }

    // ── Previous days accordion ──────────────────────────────
    document.querySelectorAll('.prev-day-head').forEach(function (head) {
      head.addEventListener('click', function () {
        var targetId = head.getAttribute('data-toggle');
        var body = document.getElementById(targetId);
        if (body) body.classList.toggle('open');
      });
    });

    // ── Search clear button ──────────────────────────────────
    var clearBtn = document.getElementById('btn-clear-search');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        window.location.href = window.location.pathname;
      });
    }

    // ── Member detail modal ──────────────────────────────────
    var overlay = document.getElementById('m-modal-overlay');
    var closeBtn = document.getElementById('m-modal-close');
    var nameEl = document.getElementById('m-modal-name');
    var idEl = document.getElementById('m-modal-id');
    var bodyEl = document.getElementById('m-modal-body');

    function fmtMoney(v) {
      var n = parseFloat(v || '0');
      if (isNaN(n)) return '—';
      return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }

    function addRow(label, value) {
      var row = document.createElement('div');
      row.className = 'm-detail-row';

      var k = document.createElement('span');
      k.className = 'k';
      k.textContent = label;

      var v = document.createElement('span');
      v.className = 'v';
      v.textContent = value || '—';

      row.appendChild(k);
      row.appendChild(v);
      bodyEl.appendChild(row);
    }

    function openModalFromCard(card) {
      var d = card.dataset;
      nameEl.textContent = d.name || '—';
      idEl.textContent = '#' + (d.uniqueId || '—');

      bodyEl.innerHTML = '';
      addRow('Checked in at', d.time);
      addRow('Phone', d.phone);
      addRow('Plan', d.plan + (d.planPrice ? ' (' + fmtMoney(d.planPrice) + ')' : ''));
      addRow('Trainer', d.trainer);
      addRow('Gender', d.gender);
      addRow('Joined on', d.doj);
      addRow('Payment status', d.paymentStatus);
      addRow('Pending amount', fmtMoney(d.pendingAmount));
      addRow('Due date', d.dueDate);
      if (d.daysRemaining !== '') {
        addRow('Days remaining', d.daysRemaining);
      }
      addRow('Last payment date', d.paymentDate);
      addRow('Address', d.address);

      if (d.isExpired === '1') {
        var badge = document.createElement('div');
        badge.className = 'badge badge-expired';
        badge.style.marginTop = '10px';
        badge.textContent = 'Membership Expired';
        bodyEl.appendChild(badge);
      } else if (d.isExpiringSoon === '1') {
        var badge2 = document.createElement('div');
        badge2.className = 'badge badge-soon';
        badge2.style.marginTop = '10px';
        badge2.textContent = 'Expiring Soon';
        bodyEl.appendChild(badge2);
      }

      overlay.classList.add('open');
    }

    document.querySelectorAll('.member-card').forEach(function (card) {
      card.addEventListener('click', function () {
        openModalFromCard(card);
      });
    });

    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        overlay.classList.remove('open');
      });
    }
    if (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) overlay.classList.remove('open');
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && overlay && overlay.classList.contains('open')) {
        overlay.classList.remove('open');
      }
    });

  });
})();