/**
 * communications/static/communications/js/communications.js
 *
 * Frontend for the Platform Communications module ONLY.
 *
 * Isolation guarantees:
 *   - Single global: `window.EnterGYMComm`. No other globals are declared.
 *   - Talks ONLY to /communications/api/* endpoints — never touches any
 *     announcements.* URL, model, or endpoint.
 *   - Never queries or mutates any `.announcement-*` / `#announcement-*`
 *     DOM node. Only operates inside #comm-popup-root and .comm-banner
 *     containers (see _popup.html / _banner.html), which announcements'
 *     own JS never touches either.
 *   - Own localStorage keys, all prefixed `comm_`, so they can never
 *     collide with any key announcements.js already uses.
 *
 * Safe to include on any page whether or not the announcements popup/
 * banner include is also present — neither script depends on the other,
 * and neither can throw because the other's DOM is missing (each only
 * looks for its own container ids/classes).
 */
(function () {
  'use strict';

  var POPUP_ROOT_ID = 'comm-popup-root';
  var API_BASE = '/communications/api/';

  var STORAGE_DISMISSED_KEY = 'comm_dismissed_ids';   // popups dismissed this session/device
  var STORAGE_SEEN_KEY = 'comm_seen_impression_ids';  // impressions already tracked, ever

  var state = {
    popupQueue: [],
    popupIndex: 0,
  };

  // ── small utilities ─────────────────────────────────────────────────

  function getCookie(name) {
    var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? decodeURIComponent(match[2]) : null;
  }

  function readIdSet(key) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function addIdToSet(key, id) {
    try {
      var ids = readIdSet(key);
      if (ids.indexOf(id) === -1) {
        ids.push(id);
        window.localStorage.setItem(key, JSON.stringify(ids));
      }
    } catch (e) {
      // localStorage unavailable (private mode, etc.) — degrade silently;
      // worst case is a repeated impression/dismiss, not a crash.
    }
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  // ── API calls ────────────────────────────────────────────────────────

  function fetchHome() {
    return fetch(API_BASE + 'home/', {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    }).then(function (res) {
      if (!res.ok) throw new Error('comm home fetch failed: ' + res.status);
      return res.json();
    });
  }

  function trackEvent(communicationId, action) {
  var csrftoken = getCookie('csrftoken');
  return fetch(API_BASE + 'track/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrftoken || '',
      'X-Requested-With': 'XMLHttpRequest',
    },
    body: JSON.stringify({ communication_id: communicationId, event: action }),  // ← key renamed 'action' → 'event'
  }).catch(function () {});
}

  function fetchUnreadCount() {
    return fetch(API_BASE + 'unread-count/', {
      method: 'GET',
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    }).then(function (res) {
      if (!res.ok) throw new Error('comm unread-count fetch failed: ' + res.status);
      return res.json();
    });
  }

  // ── rendering ────────────────────────────────────────────────────────

  function buildCardHTML(item) {
    var deepLinkKind = item.deep_link_kind || 'center';
    var deepLinkValue = item.deep_link_value || '';
    var priority = item.priority || 'medium';
    var typeDisplay = item.type_display || item.type || '';

    var imageHtml = item.image_url
      ? '<div class="comm-card-image"><img src="' + escapeHtml(item.image_url) + '" alt="" loading="lazy"></div>'
      : '';

    var actionHtml = '';
    if (deepLinkKind === 'url' && deepLinkValue) {
      actionHtml += '<a href="' + escapeHtml(deepLinkValue) + '" target="_blank" rel="noopener noreferrer" ' +
        'class="comm-btn comm-btn-primary" data-comm-action="click" data-comm-id="' + item.id + '">View</a>';
    } else if (deepLinkKind === 'screen') {
      actionHtml += '<button type="button" class="comm-btn comm-btn-primary" ' +
        'data-comm-action="click" data-comm-id="' + item.id + '" data-comm-screen="' + escapeHtml(deepLinkValue) + '">View</button>';
    }

    if (item.require_read) {
      actionHtml += '<button type="button" class="comm-btn comm-btn-secondary" ' +
        'data-comm-action="read" data-comm-id="' + item.id + '">Mark as Read</button>';
    } else {
      actionHtml += '<button type="button" class="comm-btn comm-btn-secondary" ' +
        'data-comm-action="dismissed" data-comm-id="' + item.id + '">Dismiss</button>';
    }

    return (
      '<div class="comm-card comm-priority-' + escapeHtml(priority) + '" data-comm-id="' + item.id + '">' +
        imageHtml +
        '<div class="comm-card-body">' +
          '<span class="comm-card-type">' + escapeHtml(typeDisplay) + '</span>' +
          '<h4 class="comm-card-title">' + escapeHtml(item.title) + '</h4>' +
          '<div class="comm-card-desc">' + (item.description || '') + '</div>' +
        '</div>' +
        '<div class="comm-card-actions">' + actionHtml + '</div>' +
      '</div>'
    );
  }

  function bindCardActions(container) {
    var actionEls = container.querySelectorAll('[data-comm-action]');
    for (var i = 0; i < actionEls.length; i++) {
      (function (el) {
        el.addEventListener('click', function () {
          var id = el.getAttribute('data-comm-id');
          var action = el.getAttribute('data-comm-action');
          if (!id) return;

          if (action === 'click') {
            trackEvent(id, 'clicked');
            var screen = el.getAttribute('data-comm-screen');
            if (screen) {
              // Internal deep-link screens are handled by whatever shell
              // is listening (native WebView bridge, SPA router) — this
              // script only announces the intent, it never navigates
              // directly, since it has no knowledge of the app's router.
              document.dispatchEvent(new CustomEvent('comm:navigate', { detail: { screen: screen, id: id } }));
            }
            return;
          }

          if (action === 'read' || action === 'dismissed') {
              trackEvent(id, action);
            addIdToSet(STORAGE_DISMISSED_KEY, id);
            var card = el.closest('.comm-card');
            if (card) removeFromPopupQueue(id);
          }
        });
      })(actionEls[i]);
    }
  }

  function trackImpressionsOnce(items) {
    var seen = readIdSet(STORAGE_SEEN_KEY);
    items.forEach(function (item) {
      if (seen.indexOf(item.id) === -1) {
        trackEvent(item.id, 'opened');   // ← was 'delivered', not in _VALID_EVENTS
        addIdToSet(STORAGE_SEEN_KEY, item.id);
      }
    });
  }

  // ── popup ────────────────────────────────────────────────────────────

  function renderPopupQueue() {
    var root = document.getElementById(POPUP_ROOT_ID);
    if (!root) return;

    var dismissedIds = readIdSet(STORAGE_DISMISSED_KEY);
    state.popupQueue = state.popupQueue.filter(function (item) {
      return dismissedIds.indexOf(item.id) === -1 && dismissedIds.indexOf(String(item.id)) === -1;
    });

    if (!state.popupQueue.length) {
      root.hidden = true;
      return;
    }

    state.popupIndex = 0;
    showCurrentPopup();
    root.hidden = false;
  }

  function showCurrentPopup() {
    var content = document.getElementById('comm-popup-content');
    var pagination = document.getElementById('comm-popup-pagination');
    var item = state.popupQueue[state.popupIndex];
    if (!content || !item) return;

    content.innerHTML = buildCardHTML(item);
    bindCardActions(content);

    if (pagination) {
      if (state.popupQueue.length > 1) {
        pagination.hidden = false;
        pagination.textContent = (state.popupIndex + 1) + ' / ' + state.popupQueue.length;
      } else {
        pagination.hidden = true;
      }
    }
  }

  function removeFromPopupQueue(dismissedId) {
    var root = document.getElementById(POPUP_ROOT_ID);
    if (!root) return;

    state.popupQueue = state.popupQueue.filter(function (item) {
      return String(item.id) !== String(dismissedId);
    });

    if (!state.popupQueue.length) {
      root.hidden = true;
      return;
    }

    if (state.popupIndex >= state.popupQueue.length) state.popupIndex = 0;
    showCurrentPopup();
  }

  function initPopupCloseButton() {
    var closeBtn = document.getElementById('comm-popup-close');
    if (!closeBtn) return;
    closeBtn.addEventListener('click', function () {
      var root = document.getElementById(POPUP_ROOT_ID);
      var current = state.popupQueue[state.popupIndex];
      if (current && !current.require_read) {
        trackEvent(current.id, 'dismissed');
        addIdToSet(STORAGE_DISMISSED_KEY, current.id);
      }
      if (root) root.hidden = true;
    });
  }

  // ── banners ──────────────────────────────────────────────────────────

  function renderBanners(items) {
    var placements = ['top', 'home', 'dashboard', 'carousel', 'bottom'];
    placements.forEach(function (placement) {
      var el = document.getElementById('comm-banner-' + placement);
      if (!el) return;
      var forThisPlacement = items.filter(function (item) {
        return item.banner_placement === placement;
      });
      if (!forThisPlacement.length) {
        el.hidden = true;
        el.innerHTML = '';
        return;
      }
      el.innerHTML = forThisPlacement.map(buildCardHTML).join('');
      bindCardActions(el);
      el.hidden = false;
    });
  }

  // ── unread badge (e.g. a Communications bell icon) ──────────────────

  function updateUnreadBadges(count) {
    var badges = document.querySelectorAll('[data-comm-unread-badge]');
    for (var i = 0; i < badges.length; i++) {
      var el = badges[i];
      if (count > 0) {
        el.textContent = count > 99 ? '99+' : String(count);
        el.hidden = false;
      } else {
        el.hidden = true;
      }
    }
  }

  // ── bootstrap ────────────────────────────────────────────────────────

  function init() {
    var hasPopupRoot = document.getElementById(POPUP_ROOT_ID);
    var hasAnyBanner = document.querySelector('.comm-banner');
    var hasUnreadBadge = document.querySelector('[data-comm-unread-badge]');

    if (!hasPopupRoot && !hasAnyBanner && !hasUnreadBadge) {
      // None of the Communications partials are on this page — nothing
      // to do, and no need to fire any network request.
      return;
    }

    initPopupCloseButton();

    if (hasPopupRoot || hasAnyBanner) {
      fetchHome()
        .then(function (data) {
          var popup = data.popup;
          var popups = popup ? [popup] : [];
          var banners = data.banner || [];

          trackImpressionsOnce(popups.concat(banners));

          state.popupQueue = popups.slice();
          renderPopupQueue();
          renderBanners(banners);
        })
        .catch(function (err) {
          // Fail silently in production — Communications is additive; a
          // failed fetch must never surface an error to the visitor or
          // block the rest of the page (including Announcements).
          if (window.console && console.warn) {
            console.warn('[communications] home fetch failed', err);
          }
        });
    }

    if (hasUnreadBadge) {
      fetchUnreadCount()
        .then(function (data) { updateUnreadBadges(data.unread_count || 0); })
        .catch(function () {});
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Minimal public surface for other scripts on the page (e.g. a mobile
  // WebView bridge that wants to force a refresh after coming back to
  // the foreground) — deliberately small.
  window.EnterGYMComm = {
    refresh: init,
  };
})();