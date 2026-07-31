/**
 * announcements/static/announcements/js/announcements.js
 *
 * Drives:
 *   - Home-open popup (website + Android WebView, since the WebView loads
 *     the same server-rendered pages — no separate native implementation
 *     needed, consistent with how geo_attendance.js / RefreshTrigger.js
 *     already work for this project).
 *   - Scrolling website banner.
 *   - Read / Dismiss actions -> /api/announcements/read/ and /dismiss/.
 *   - Announcement Center card "mark read on view" for require_read items.
 *
 * Respects popup_behavior from the spec:
 *   allow_multiple_popups: false  -> only ever one popup open at a time
 *   show_again_after_read: false  -> handled server-side (api_home excludes it)
 *   show_highest_priority_first   -> handled server-side (priority_rank sort)
 */
(function () {
  'use strict';

  function getCookie(name) {
    var match = document.cookie.match('(^|;\\s*)' + name + '=([^;]*)');
    return match ? decodeURIComponent(match[2]) : null;
  }

  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || '',
      },
      body: JSON.stringify(body),
    }).catch(function (err) {
      console.warn('[Announcements] request failed', err);
    });
  }

  function markRead(id) { return postJSON('/api/announcements/read/', { announcement_id: id }); }
  function markDismissed(id) { return postJSON('/api/announcements/dismiss/', { announcement_id: id }); }

  // ── Popup ────────────────────────────────────────────────────────────
  function renderPopup(a) {
    var overlay = document.getElementById('annPopupOverlay');
    if (!overlay) return;

    document.getElementById('annPopupBadge').textContent = a.priority.toUpperCase();
    document.getElementById('annPopupBadge').className = 'ann-badge ann-badge-' + a.priority;
    document.getElementById('annPopupTitle').textContent = a.title;
    document.getElementById('annPopupDescription').innerHTML = a.description;

    var img = document.getElementById('annPopupImage');
    if (a.image_url) { img.src = a.image_url; img.style.display = ''; } else { img.style.display = 'none'; }

    var link = document.getElementById('annPopupLink');
    if (a.external_link) { link.href = a.external_link; link.style.display = ''; } else { link.style.display = 'none'; }

    var dismissBtn = document.getElementById('annPopupDismiss');
    var readBtn = document.getElementById('annPopupMarkRead');
    var closeBtn = document.getElementById('annPopupClose');

    // High priority + require_read: dismiss alone doesn't satisfy the
    // requirement, so hide Dismiss and force Mark as Read.
    if (a.priority === 'high' && a.require_read) {
      dismissBtn.style.display = 'none';
    } else {
      dismissBtn.style.display = '';
    }

    function close() { overlay.style.display = 'none'; }

    dismissBtn.onclick = function () { markDismissed(a.id); close(); };
    readBtn.onclick = function () { markRead(a.id); close(); };
    closeBtn.onclick = function () {
      // X behaves like dismiss unless read is mandatory.
      if (a.priority === 'high' && a.require_read) return; // block hard-close
      markDismissed(a.id);
      close();
    };

    overlay.style.display = 'flex';
  }

  // ── Banner ───────────────────────────────────────────────────────────
  function renderBanner(list) {
    var banner = document.getElementById('annScrollBanner');
    var track = document.getElementById('annScrollTrack');
    if (!banner || !track || !list.length) return;

    track.innerHTML = '';
    list.forEach(function (a) {
      var item = document.createElement('span');
      item.className = 'ann-scroll-item';
      item.textContent = '📢 ' + a.title;
      item.addEventListener('click', function () {
        if (a.deep_link) window.location.href = a.deep_link;
      });
      track.appendChild(item);
    });
    banner.style.display = '';
  }

  function loadHomeAnnouncements() {
    fetch('/api/announcements/home/', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        if (data.popup) renderPopup(data.popup);
        if (data.banner && data.banner.length) renderBanner(data.banner);
      })
      .catch(function (err) { console.warn('[Announcements] home fetch failed', err); });
  }

  // ── Announcement Center: mark-as-read on click for cards that require it ─
  function wireCenterCards() {
    document.querySelectorAll('.ann-card[data-id]').forEach(function (card) {
      card.addEventListener('click', function () {
        markRead(card.getAttribute('data-id'));
      }, { once: true });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (document.getElementById('annPopupOverlay') || document.getElementById('annScrollBanner')) {
      loadHomeAnnouncements();
    }
    if (document.querySelector('.ann-card[data-id]')) {
      wireCenterCards();
    }
  });
})();
