// dashboard/js/members.js

document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll('.db-member-actions [data-bs-toggle="dropdown"]').forEach(function (toggleEl) {
    bootstrap.Dropdown.getOrCreateInstance(toggleEl, {
      popperConfig: function (defaultConfig) {
        return Object.assign({}, defaultConfig, {
          strategy: 'fixed',
          modifiers: [
            ...defaultConfig.modifiers,
            {
              name: 'flip',
              options: {
                fallbackPlacements: ['top-end', 'bottom-end', 'top-start']
              }
            },
            {
              name: 'preventOverflow',
              options: {
                boundary: document.body,
                padding: 8
              }
            }
          ]
        });
      }
    });
  });

  // ── Filters form ──────────────────────────────────────────────
  var form = document.getElementById("memberFiltersForm");
  var panel = document.getElementById("membersFiltersPanel");
  var toggleBtn = document.getElementById("toggleFiltersBtn");

  if (form) {
    form.querySelectorAll("select").forEach(function (el) {
      el.addEventListener("change", function () {
        form.submit();
      });
    });
  }

  document.querySelectorAll(".db-member-row, .db-member-card").forEach(function (row) {
    row.addEventListener("click", function (e) {
      if (e.target.closest(".db-member-actions")) return;
      var href = row.getAttribute("data-href");
      if (href) window.location.href = href;
    });
  });

  // ── Filter panel toggle ──────────────────────────────────────
  // Always starts collapsed, regardless of active filters in the
  // querystring. Opens only on explicit "Filters" button click.
  // Closes again on submit.
  if (panel && toggleBtn && form) {
    toggleBtn.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
    });

    form.addEventListener("submit", function () {
      panel.hidden = true;
    });
  }
});
document.addEventListener('click', function (e) {
  const btn = e.target.closest('.db-mark-attendance-btn');
  if (!btn) return;

  e.preventDefault();
  e.stopPropagation();

  const memberId = btn.dataset.memberId;
  btn.disabled = true;

  fetch(`/members/${memberId}/mark-attendance/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)?.[1],
    },
  })
    .then(res => res.json().then(data => ({ status: data.status, data })))
    .then(({ status, data }) => {
      // "success" already shows a nice toast via the websocket
      // (live-attendance.js) — so we only need to handle the
      // other two cases here.
      if (status === 'success') return;
      showSimpleToast(data.message, status === 'exists' ? 'exists' : 'error');
    })
    .catch(() => showSimpleToast('Something went wrong. Try again.', 'error'))
    .finally(() => { btn.disabled = false; });
});

function showSimpleToast(message, type) {
  const container = document.getElementById('lacContainer');
  if (!container) return;

  const iconMap = {
    error: 'bi-exclamation-circle-fill',
    exists: 'bi-clock-history',
    info: 'bi-info-circle-fill',
  };
  const titleMap = {
    error: 'Error',
    exists: 'Already Marked',
    info: 'Notice',
  };

  const toast = document.createElement('div');
  toast.className = `lac-toast lac-toast-${type}`;
  toast.innerHTML = `
    <div class="lac-toast-header">
      <i class="bi ${iconMap[type] || iconMap.info}"></i>
      <span class="lac-toast-title">${titleMap[type] || 'Notice'}</span>
      <button class="lac-toast-close" aria-label="Dismiss">&times;</button>
    </div>
    <div class="lac-toast-body"><div class="lac-toast-simple-body">${message}</div></div>
  `;
  toast.querySelector('.lac-toast-close').addEventListener('click', () => {
    toast.classList.add('lac-out');
    setTimeout(() => toast.remove(), 300);
  });
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('lac-in'));
  setTimeout(() => {
    toast.classList.add('lac-out');
    setTimeout(() => toast.remove(), 300);
  }, 6000);
}