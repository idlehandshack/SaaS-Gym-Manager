/**
 * Global draggable refresh FAB.
 * - Click/tap (no drag) => reloads the page via window.location.reload()
 * - Pointer drag => repositions the button anywhere in the viewport
 * - Position is persisted per-device in localStorage and clamped on resize
 * - No polling/intervals; only pointer + click + resize/viewport listeners
 */
(function () {
  const STORAGE_KEY = 'db_refresh_fab_pos_v1';
  const DRAG_THRESHOLD = 6;
  const EDGE_MARGIN = 8;

  document.addEventListener('DOMContentLoaded', function () {
    const wrap = document.getElementById('dbRefreshFabWrap');
    const btn = document.getElementById('dbRefreshFabBtn');
    if (!wrap || !btn) return;

    let dragging = false;
    let moved = false;
    let startX = 0, startY = 0;
    let startLeft = 0, startTop = 0;
    let reloadTriggered = false;

    function viewportSize() {
      // visualViewport is more accurate inside WebViews / with mobile keyboards
      if (window.visualViewport) {
        return { w: window.visualViewport.width, h: window.visualViewport.height };
      }
      return { w: window.innerWidth, h: window.innerHeight };
    }

    function clamp(val, min, max) {
      return Math.min(Math.max(val, min), max);
    }

    function applyPosition(left, top, { persist = true } = {}) {
      const rect = wrap.getBoundingClientRect();
      const w = rect.width || wrap.offsetWidth || 40;
      const h = rect.height || wrap.offsetHeight || 40;
      const vp = viewportSize();

      const maxLeft = vp.w - w - EDGE_MARGIN;
      const maxTop = vp.h - h - EDGE_MARGIN;

      left = clamp(left, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxLeft));
      top = clamp(top, EDGE_MARGIN, Math.max(EDGE_MARGIN, maxTop));

      wrap.style.left = left + 'px';
      wrap.style.top = top + 'px';
      wrap.style.right = 'auto';
      wrap.style.bottom = 'auto';

      if (persist) {
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify({ left, top }));
        } catch (e) {}
      }
    }

    function defaultPosition() {
      const rect = wrap.getBoundingClientRect();
      const h = rect.height || wrap.offsetHeight || 40;
      const vp = viewportSize();
      return { left: 20, top: vp.h - h - 90 };
    }

    function restorePosition() {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
      } catch (e) {
        saved = null;
      }

      const pos = (saved && typeof saved.left === 'number' && typeof saved.top === 'number')
        ? saved
        : defaultPosition();

      applyPosition(pos.left, pos.top, { persist: false });

      // Re-clamp on the next frame once layout/size is fully settled —
      // this catches WebViews where offsetWidth/Height or the viewport
      // size isn't final at DOMContentLoaded time.
      requestAnimationFrame(function () {
        const rect = wrap.getBoundingClientRect();
        applyPosition(rect.left, rect.top, { persist: false });
      });
    }

    function reclampToViewport() {
      const rect = wrap.getBoundingClientRect();
      applyPosition(rect.left, rect.top);
    }

    function onPointerDown(e) {
      if (btn.disabled) return;
      dragging = true;
      moved = false;
      reloadTriggered = false;

      const rect = wrap.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      startX = e.clientX;
      startY = e.clientY;

      btn.classList.add('dragging');
      try { btn.setPointerCapture(e.pointerId); } catch (err) {}
    }

    function onPointerMove(e) {
      if (!dragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      if (!moved && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) {
        moved = true;
      }
      if (moved) {
        applyPosition(startLeft + dx, startTop + dy);
      }
    }

    function onPointerUp(e) {
      if (!dragging) return;
      dragging = false;
      btn.classList.remove('dragging');
      try { btn.releasePointerCapture(e.pointerId); } catch (err) {}

      if (!moved) {
        triggerReload();
      }
    }

    function triggerReload() {
      if (reloadTriggered || btn.disabled) return;
      reloadTriggered = true;
      btn.disabled = true;
      btn.setAttribute('aria-disabled', 'true');
      btn.classList.add('spinning');
      window.location.reload();
    }

    btn.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    window.addEventListener('pointercancel', onPointerUp);

    btn.addEventListener('click', function (e) {
      if (moved) {
        e.preventDefault();
        moved = false;
        return;
      }
      triggerReload();
    });

    // Standard resize (desktop browsers, orientation change)
    window.addEventListener('resize', reclampToViewport);

    // WebViews / mobile browsers often fire visualViewport changes
    // (keyboard open/close, safe-area shifts) without a window 'resize'.
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', reclampToViewport);
      window.visualViewport.addEventListener('scroll', reclampToViewport);
    }

    // Re-clamp once more after full page load, in case images/fonts
    // shifted layout after DOMContentLoaded.
    window.addEventListener('load', reclampToViewport);

    restorePosition();
  });
})();