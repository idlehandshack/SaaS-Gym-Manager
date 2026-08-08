/* =============================================================================
   EnterGYM — saas_home.js
   Sections:
   1. Shared utilities
   2. Nav scroll
   3. Animated counters
   4. Scroll reveal
   5. Pricing tab switcher
   6. Plan calculator
   7. Testimonials carousel
   8. Hero video — robust autoplay & resume
============================================================================= */


/* -----------------------------------------------------------------------------
   1. SHARED UTILITIES
----------------------------------------------------------------------------- */

function fmtCount(n) {
  /* FIX: explicit branch for large numbers (100K+) avoids floating-point
     display issues and makes intent clear for future maintainers. */
  if (n >= 100000) return Math.round(n / 1000) + "K";
  if (n >= 1000)   return Math.round(n / 1000) + "K";
  return String(n);
}

function fmtRupee(n) {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

function onEnter(elements, callback, options) {
  var obs = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) {
        callback(e.target);
        obs.unobserve(e.target);
      }
    });
  }, options || { threshold: 0.1 });
  elements.forEach(function (el) { obs.observe(el); });
  return obs;
}


/* -----------------------------------------------------------------------------
   2. NAV SCROLL
----------------------------------------------------------------------------- */
(function () {
  var nav = document.getElementById("siteNav");
  if (!nav) return;

  window.addEventListener("scroll", function () {
    nav.classList.toggle("is-scrolled", window.scrollY > 8);
  }, { passive: true });
})();


/* -----------------------------------------------------------------------------
   3. ANIMATED COUNTERS
----------------------------------------------------------------------------- */
(function () {
  if (!("IntersectionObserver" in window)) return;

  var nums = document.querySelectorAll(".proof-num[data-target]");
  if (!nums.length) return;

  function animateCounter(el) {
    var target = +el.dataset.target;
    var dur    = 1600;
    var start  = performance.now();

    function step(now) {
      var t    = Math.min((now - start) / dur, 1);
      var ease = 1 - Math.pow(1 - t, 3);
      el.textContent = fmtCount(Math.round(ease * target));
      if (t < 1) requestAnimationFrame(step);
      else        el.textContent = fmtCount(target);
    }
    requestAnimationFrame(step);
  }

  onEnter(Array.from(nums), animateCounter, { threshold: 0.5 });
})();


/* -----------------------------------------------------------------------------
   4. SCROLL REVEAL
   NOTE: The inline <script> in saas_home.html also had a scroll-reveal block
   that targeted the same selectors. That block must be REMOVED from the HTML
   to avoid a race condition where two observers compete on the same elements.
   Only this block should run.
----------------------------------------------------------------------------- */
(function () {
  if (!("IntersectionObserver" in window)) return;

  var SELECTORS = [
    ".feat-row",
    ".feat-mini",
    ".gym-card",
    ".how-step",
    ".testi-card",
    ".s6-layout",
    ".mp-card",
    ".gw-card",
  ].join(", ");

  var els = Array.from(document.querySelectorAll(SELECTORS));
  if (!els.length) return;

  /* Guard: skip elements that are already animated (prevents double-add
     if the HTML inline script wasn't removed yet). */
  els.forEach(function (el) {
    if (!el.classList.contains("reveal")) el.classList.add("reveal");
  });

  onEnter(els, function (el) { el.classList.add("visible"); }, { threshold: 0.1 });
})();


/* -----------------------------------------------------------------------------
   5. PRICING TAB SWITCHER
----------------------------------------------------------------------------- */
(function () {
  var tabs      = Array.from(document.querySelectorAll(".p-tabs .p-tab"));
  var panels    = Array.from(document.querySelectorAll(".p-panel"));
  var indicator = document.getElementById("tabIndicator");
  if (!tabs.length) return;

  var hasGSAP = typeof window.gsap !== "undefined";
  if (hasGSAP && window.ScrollTrigger) gsap.registerPlugin(ScrollTrigger);

  /* ---- count-up price animation ---- */
  function countUp(el) {
    var target = parseFloat(el.dataset.count);
    if (isNaN(target)) return;

    if (!hasGSAP) {
      el.textContent = fmtRupee(target);
      return;
    }
    var obj = { val: 0 };
    gsap.to(obj, {
      val: target,
      duration: 1.1,
      ease: "power2.out",
      onUpdate: function () { el.textContent = fmtRupee(obj.val); }
    });
  }

  /* ---- sliding tab indicator ---- */
  function moveIndicator(tab) {
    if (!indicator || !tab) return;
    var tabsRect = tab.parentElement.getBoundingClientRect();
    var rect     = tab.getBoundingClientRect();
    var x = rect.left - tabsRect.left - 6;

    if (hasGSAP) {
      gsap.to(indicator, { x: x, duration: 0.45, ease: "power3.out" });
    } else {
      indicator.style.transform = "translateX(" + x + "px)";
    }
  }

  /* ---- entrance animation for cards inside a panel ---- */
  function animatePanelIn(panel) {
    var cards = panel.querySelectorAll(".p-card, .p-grid-card");
    if (!cards.length) return;

    if (hasGSAP) {
      gsap.fromTo(cards, { y: 28, opacity: 0 }, {
        y: 0, opacity: 1, duration: 0.6, stagger: 0.1, ease: "power3.out"
      });
    } else {
      cards.forEach(function (c) { c.style.opacity = 1; });
    }
  }

  /* ---- hover tilt on cards ---- */
  function wireTilt(card) {
    if (!hasGSAP) return;
    card.addEventListener("mousemove", function (e) {
      var r = card.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width - 0.5;
      var y = (e.clientY - r.top) / r.height - 0.5;
      gsap.to(card, {
        rotateY: x * 4, rotateX: -y * 4,
        transformPerspective: 800, duration: 0.4, ease: "power2.out"
      });
    });
    card.addEventListener("mouseleave", function () {
      gsap.to(card, { rotateY: 0, rotateX: 0, duration: 0.5, ease: "power3.out" });
    });
  }
  document.querySelectorAll(".p-card, .p-grid-card").forEach(wireTilt);

  /* ---- tab click handling ---- */
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      if (tab.classList.contains("is-active")) return;

      tabs.forEach(function (t) { t.classList.remove("is-active"); });
      tab.classList.add("is-active");
      moveIndicator(tab);

      var targetId = tab.dataset.panel;
      var current  = document.querySelector(".p-panel.is-active");
      var next     = document.getElementById(targetId);
      if (!next || next === current) return;

      if (hasGSAP) {
        gsap.to(current, {
          opacity: 0, y: -12, duration: 0.25, ease: "power2.in",
          onComplete: function () {
            current.classList.remove("is-active");
            next.classList.add("is-active");
            gsap.set(next, { opacity: 0, y: 16 });
            gsap.to(next, { opacity: 1, y: 0, duration: 0.45, ease: "power3.out" });
            animatePanelIn(next);
            next.querySelectorAll("[data-count]").forEach(countUp);
          }
        });
      } else {
        current.classList.remove("is-active");
        next.classList.add("is-active");
        next.querySelectorAll("[data-count]").forEach(countUp);
      }
    });
  });

  /* ---- scroll-triggered entrance for the section head + tabs ---- */
  if (hasGSAP && window.ScrollTrigger) {
    var pricingSection = document.querySelector(".pricing");
    if (pricingSection) {
      gsap.from(".p-eyebrow, .p-heading, .p-body", {
        scrollTrigger: { trigger: ".pricing", start: "top 75%" },
        y: 24, opacity: 0, duration: 0.7, stagger: 0.08, ease: "power3.out"
      });
      gsap.from(".p-tabs", {
        scrollTrigger: { trigger: ".p-tabs", start: "top 80%" },
        y: 20, opacity: 0, duration: 0.7, delay: 0.15, ease: "power3.out"
      });
    }
  }

  /* ---- init ---- */
  var initialTab = document.querySelector(".p-tab.is-active") || tabs[0];
  animatePanelIn(document.querySelector(".p-panel.is-active") || panels[0]);
  document.querySelectorAll("#panel-launchpad [data-count]").forEach(countUp);

  window.addEventListener("load", function () { moveIndicator(initialTab); });
  window.addEventListener("resize", function () {
    moveIndicator(document.querySelector(".p-tab.is-active"));
  });
})();

/* -----------------------------------------------------------------------------
   5b. MOBILE PRICE CAROUSEL (Fixed Subscription only)
   3 cards: center is expanded with full detail, left/right are collapsed to
   price-only. Tap a side card, or swipe, to bring it to center — smooth,
   circular 3-item rotation, no jank. Desktop is untouched (see the
   `mq.matches` guard below); this only ever runs under 820px.
----------------------------------------------------------------------------- */
(function () {
  var grid = document.querySelector("#panel-fixed .p-grid");
  if (!grid) return;

  var cards = Array.from(grid.querySelectorAll(".p-grid-card"));
  if (cards.length < 3) return;

  var mq = window.matchMedia("(max-width: 820px)");
  var SLOT_CLASSES = ["p-slot-left", "p-slot-center", "p-slot-right"];

  /* order[slot] = index into `cards`. slot 0 = left, 1 = center, 2 = right */
  var defaultCenter = cards.findIndex(function (c) {
    return c.classList.contains("p-grid-card--popular");
  });
  if (defaultCenter === -1) defaultCenter = 1;

  var order = toCenterOrder(defaultCenter);

  function toCenterOrder(idx) {
    var others = cards.map(function (_, i) { return i; }).filter(function (i) { return i !== idx; });
    return [others[0], idx, others[1]];
  }

  function clearSlotClasses(card) {
    card.classList.remove(SLOT_CLASSES[0], SLOT_CLASSES[1], SLOT_CLASSES[2]);
  }

  function render() {
    if (!mq.matches) {
      cards.forEach(clearSlotClasses);
      grid.style.height = "";
      return;
    }

    order.forEach(function (cardIdx, slot) {
      var card = cards[cardIdx];
      clearSlotClasses(card);
      card.classList.add(SLOT_CLASSES[slot]);
    });

    var centerCard = cards[order[1]];
    // Measure after layout settles so height is accurate, and only if the
    // panel is actually visible (offsetParent is null when display:none).
    requestAnimationFrame(function () {
      if (centerCard.offsetParent === null) return;
      grid.style.height = centerCard.offsetHeight + "px";
    });
  }

  function rotate(direction) {
    // direction 1 = forward (right card becomes center)
    // direction -1 = backward (left card becomes center)
    order = direction === 1
      ? [order[1], order[2], order[0]]
      : [order[2], order[0], order[1]];
    render();
  }

  cards.forEach(function (card) {
    card.addEventListener("click", function () {
      if (!mq.matches) return;
      if (card.classList.contains("p-slot-left"))  rotate(-1);
      else if (card.classList.contains("p-slot-right")) rotate(1);
    });
  });

  /* ---- swipe ---- */
  var startX = 0, deltaX = 0, dragging = false;
  var SWIPE_THRESHOLD = 40;

  grid.addEventListener("touchstart", function (e) {
    if (!mq.matches) return;
    dragging = true;
    startX = e.touches[0].clientX;
    deltaX = 0;
  }, { passive: true });

  grid.addEventListener("touchmove", function (e) {
    if (!dragging) return;
    deltaX = e.touches[0].clientX - startX;
  }, { passive: true });

  grid.addEventListener("touchend", function () {
    if (!dragging) return;
    dragging = false;
    if (deltaX <= -SWIPE_THRESHOLD)      rotate(1);
    else if (deltaX >= SWIPE_THRESHOLD)  rotate(-1);
    deltaX = 0;
  });

  /* ---- re-measure whenever the Fixed Subscription tab becomes active,
     since its panel was display:none (offsetHeight 0) until now ---- */
  var panel = document.getElementById("panel-fixed");
  if (panel && "MutationObserver" in window) {
    new MutationObserver(function () {
      if (panel.classList.contains("is-active")) render();
    }).observe(panel, { attributes: true, attributeFilter: ["class"] });
  }

  /* ---- breakpoint crossing + resize ---- */
  if (mq.addEventListener) mq.addEventListener("change", render);
  else if (mq.addListener) mq.addListener(render); // Safari <14 fallback

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(render, 120);
  });

  render();
})();
(function () {
  function isMobile() { return window.matchMedia('(max-width: 760px)').matches; }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-see-more]');
    if (!btn) return;

    var textEl = btn.previousElementSibling;
    if (!textEl || !textEl.classList.contains('testi-quote-text')) return;

    var expanded = textEl.classList.toggle('is-expanded');
    btn.textContent = expanded ? 'See less' : 'See more';

    // Keep the carousel stage height in sync with the now-taller/shorter card.
    var stage = document.getElementById('testiStage');
    var card  = btn.closest('.testi-card');
    if (stage && card && card.classList.contains('is-active')) {
      stage.style.minHeight = card.offsetHeight + 'px';
    }
  });

  // Hide "See more" entirely if the text doesn't actually overflow 5 lines,
  // and hide it outright on desktop widths.
  function refreshToggles() {
    document.querySelectorAll('.testi-quote-text').forEach(function (textEl) {
      var btn = textEl.nextElementSibling;
      if (!btn || !btn.hasAttribute('data-see-more')) return;

      if (!isMobile()) {
        btn.style.display = 'none';
        return;
      }
      btn.style.display = '';
      textEl.classList.remove('is-expanded');
      btn.textContent = 'See more';

      // If content fits within 5 lines already, no need for the button.
      var overflowing = textEl.scrollHeight > textEl.clientHeight + 2;
      btn.style.visibility = overflowing ? 'visible' : 'hidden';
    });
  }

  window.addEventListener('resize', refreshToggles);
  window.addEventListener('load', refreshToggles);
  document.addEventListener('DOMContentLoaded', refreshToggles);
})();
/* -----------------------------------------------------------------------------
   8. HERO VIDEO — robust autoplay & resume
----------------------------------------------------------------------------- */
(function () {
  var video = document.querySelector(".g-video");
  if (!video) return;

  /* Attempt play; swallow NotAllowedError silently */
  function tryPlay() {
    if (video.paused && !video.ended) {
      var p = video.play();
      if (p && typeof p.catch === "function") {
        p.catch(function () { /* autoplay blocked by browser policy — ignore */ });
      }
    }
  }

  /* ① Kick immediately (readyState may already be enough) */
  if (video.readyState >= 2) {
    tryPlay();
  } else {
    video.addEventListener("canplay", tryPlay, { once: true });
  }

  /* ② Tab visibility restored */
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) tryPlay();
  });

  /* ③ iOS back-forward cache restore */
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) tryPlay();
  });

  /* ④ Heartbeat poll — catches Low Power Mode & mid-session suspensions */
  var pollTimer = setInterval(function () {
    if (video.paused && !video.ended && !document.hidden) {
      tryPlay();
    }
  }, 4000);

  /* Stop polling when page is unloaded to avoid memory leaks */
  window.addEventListener("pagehide", function () { clearInterval(pollTimer); });

  /* ⑤ Re-play when video element scrolls back into viewport */
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) tryPlay();
      });
    }, { threshold: 0.25 }).observe(video);
  }
})();

/* -----------------------------------------------------------------------------
   9. PROBLEM SECTION — STACKED COMPARISON CARDS
   Append this as a new numbered section in saas_home.js (after section 8).
   Swaps which card ("before" / "after") is on top of the deck. Only the
   back card is clickable; front card is display-only (pointer-events:none
   is toggled via the CSS [data-state] rules based on data-state below).
----------------------------------------------------------------------------- */
(function () {
  var stack = document.querySelector(".problem-stack");
  if (!stack) return;

  var beforeCard = stack.querySelector('[data-card="before"]');
  var afterCard  = stack.querySelector('[data-card="after"]');
  if (!beforeCard || !afterCard) return;

  var swapped = false; // false = before in front, true = after in front

  function sync() {
    stack.classList.toggle("is-swapped", swapped);

    var frontCard = swapped ? afterCard : beforeCard;
    var backCard  = swapped ? beforeCard : afterCard;

    frontCard.setAttribute("data-state", "front");
    frontCard.setAttribute("aria-pressed", "true");

    backCard.setAttribute("data-state", "back");
    backCard.setAttribute("aria-pressed", "false");
  }

  function swap() {
    swapped = !swapped;
    sync();
  }

  function handleActivate(e) {
    var card = e.currentTarget;
    // Only the back card responds — CSS already blocks pointer events on
    // the front card, but guard here too for keyboard/programmatic focus.
    if (card.getAttribute("data-state") !== "back") return;
    swap();
  }

  [beforeCard, afterCard].forEach(function (card) {
    card.addEventListener("click", handleActivate);
    card.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        handleActivate(e);
      }
    });
  });

  sync();
})();
(function () {
  var marquee = document.querySelector(".hero-logo-marquee");
  var track   = document.querySelector(".hero-logo-track");
  if (!marquee || !track) return;

  // Treat the FIRST half of the rendered images as the canonical
  // "one set" unit — capture its markup once, then rebuild the track
  // with however many copies are needed to fully cover the container
  // (plus one extra unit of buffer), so the belt never runs out of
  // content mid-loop no matter how few logos there are.
  var allImgs = Array.from(track.querySelectorAll("img"));
  if (allImgs.length === 0) return;
  var half = Math.floor(allImgs.length / 2) || allImgs.length;
  var baseSetHTML = allImgs.slice(0, half).map(function (img) {
    return img.outerHTML;
  }).join("");

  function rebuildAndMeasure() {
    var containerWidth = marquee.getBoundingClientRect().width;
    if (!containerWidth) return false;

    // Render TWO copies of one set so we can measure the true repeat
    // period (content width + the gap before the next copy) — measuring
    // only inside a single set misses that trailing gap and causes a
    // small snap at every loop restart.
    track.innerHTML = baseSetHTML + baseSetHTML;
    var imgs = Array.from(track.querySelectorAll("img"));
    var half = imgs.length / 2;
    var setWidth = imgs.length
      ? imgs[half].getBoundingClientRect().left - imgs[0].getBoundingClientRect().left
      : 0;
    if (!setWidth) return false;

    var copies = Math.max(2, Math.ceil(containerWidth / setWidth) + 1);
    track.innerHTML = baseSetHTML.repeat(copies);

    track.style.setProperty("--marquee-distance", setWidth + "px");
    return true;
  }

  function start() {
    if (rebuildAndMeasure()) {
      track.classList.add("is-ready");
    }
  }

  // Wait for images (of the ORIGINAL single set) to load so measured
  // widths are accurate, then build.
  var imgs = allImgs.slice(0, half);
  var pending = imgs.length;
  function onOneSettled() {
    pending -= 1;
    if (pending <= 0) start();
  }
  if (pending === 0) {
    start();
  } else {
    imgs.forEach(function (img) {
      if (img.complete) {
        onOneSettled();
      } else {
        img.addEventListener("load", onOneSettled, { once: true });
        img.addEventListener("error", onOneSettled, { once: true });
      }
    });
  }

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      track.classList.remove("is-ready");
      void track.offsetWidth;
      if (rebuildAndMeasure()) {
        track.classList.add("is-ready");
      }
    }, 150);
  });
})();
/* -----------------------------------------------------------------------------
   10. FEATURE CARDS → DETAIL MODAL
----------------------------------------------------------------------------- */
(function () {
  var grid  = document.getElementById("featGrid");
  var modal = document.getElementById("featModal");
  if (!grid || !modal) return;

  var overlay   = document.getElementById("featModalOverlay");
  var closeBtn  = document.getElementById("featModalClose");
  var indexEl   = document.getElementById("featModalIndex");
  var titleEl   = document.getElementById("featModalTitle");
  var bodyEl    = document.getElementById("featModalBody");
  var lastFocus = null;

  function openModal(card) {
    var index  = card.querySelector(".feat-index");
    var title  = card.querySelector("h3");
    var detail = card.querySelector(".feat-detail");
    if (!title || !detail) return;

    indexEl.textContent = index ? index.textContent : "";
    titleEl.innerHTML    = title.innerHTML;
    bodyEl.innerHTML     = detail.innerHTML;

    lastFocus = document.activeElement;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    closeBtn.focus();
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }

  Array.from(grid.querySelectorAll(".feat-card")).forEach(function (card) {
    card.addEventListener("click", function () { openModal(card); });
  });

  overlay.addEventListener("click", closeModal);
  closeBtn.addEventListener("click", closeModal);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });
})();
/* =============================================================================
   MEMBER PORTAL — interactive phone showcase
   Hotspot → connector line → floating label, anatomy-diagram style.
   Legend is hidden but stays in the DOM for JS wiring / a11y fallback.
   Phone elements (data-target) are the primary interaction surface on
   both desktop (hover) and mobile (tap). Idle-cycles through every
   hotspot until the user engages.
============================================================================= */
(function () {
  var section = document.getElementById("member-portal");
  if (!section) return;

  var stage  = document.getElementById("mpStage");
  var svg    = document.getElementById("mpLines");
  var path   = document.getElementById("mpLinePath");
  var legend = document.getElementById("mpLegend");
  if (!stage || !svg || !path || !legend) return;

  var HOTSPOTS = ["plan", "phone", "payment", "status", "trainer"];

  var SLOTS = {
    plan:    { x: 0.02, y: 0.20, align: "left"  },
    phone:   { x: 0.02, y: 0.44, align: "left"  },
    payment: { x: 0.02, y: 0.80, align: "left"  },
    status:  { x: 0.98, y: 0.30, align: "right" },
    trainer: { x: 0.98, y: 0.60, align: "right" }
  };

  var isTouch      = window.matchMedia("(hover: none)").matches;
  var isMobileSize = window.matchMedia("(max-width: 640px)").matches;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var dot = document.createElement("div");
  dot.className = "mp-dot";
  stage.appendChild(dot);

  var tooltip = document.createElement("div");
  tooltip.className = "mp-tooltip";
  tooltip.setAttribute("role", "status");
  stage.appendChild(tooltip);

  var COPY = {
    plan:    { title: "Membership Plan",   sub: "Duration & price" },
    phone:   { title: "Phone Number",      sub: "Contact on file" },
    payment: { title: "Payment Status",    sub: "Pending & paid, at a glance" },
    status:  { title: "Membership Status", sub: "Days remaining, live" },
    trainer: { title: "Pending Amount",    sub: "Outstanding balance" }
  };

  var labels = {}, legendItems = {}, targets = {};

  HOTSPOTS.forEach(function (id) {
    labels[id] = document.getElementById("mpLabel-" + id);
    var t = section.querySelector('[data-target="' + id + '"]');
    if (t) targets[id] = t;
  });
  Array.from(legend.querySelectorAll(".mp-legend-item")).forEach(function (el) {
    legendItems[el.dataset.hotspot] = el;
  });

  var active = null;

  function stageRect() { return stage.getBoundingClientRect(); }

  function positionLabel(id) {
    var slot = SLOTS[id];
    var label = labels[id];
    if (!slot || !label) return;
    var rect = stageRect();
    var y = slot.y * rect.height;

    label.style.top = y + "px";
    if (slot.align === "left") {
      label.style.left = (slot.x * rect.width) + "px";
      label.style.right = "auto";
    } else {
      label.style.left = "auto";
      label.style.right = ((1 - slot.x) * rect.width) + "px";
    }
  }

  // Cubic Bézier from the hotspot (on the phone) to its label anchor —
  // control points pulled horizontally so the curve reads as a deliberate
  // sweep. Each hotspot owns a unique y-slot so lines never collide.
  function drawLine(id) {
    var target = targets[id];
    var label = labels[id];
    if (!target || !label) return;

    var sRect = stageRect();
    var tRect = target.getBoundingClientRect();
    var lRect = label.getBoundingClientRect();

    var dotX = tRect.left + tRect.width / 2 - sRect.left;
    var dotY = tRect.top + tRect.height / 2 - sRect.top;

    var slot = SLOTS[id];
    var labelAnchorX = slot.align === "left" ? lRect.right - sRect.left : lRect.left - sRect.left;
    var labelAnchorY = lRect.top + lRect.height / 2 - sRect.top;

    var pull = Math.max(40, Math.abs(dotX - labelAnchorX) * 0.45);
    var c1x = dotX + (slot.align === "left" ? -pull : pull);
    var c1y = dotY;
    var c2x = labelAnchorX + (slot.align === "left" ? pull * 0.4 : -pull * 0.4);
    var c2y = labelAnchorY;

    dot.style.left = dotX + "px";
    dot.style.top  = dotY + "px";

    var d = "M" + dotX + "," + dotY +
            " C" + c1x + "," + c1y + " " + c2x + "," + c2y + " " + labelAnchorX + "," + labelAnchorY;
    path.setAttribute("d", d);

    if (!reduceMotion) {
      var len = path.getTotalLength();
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.classList.remove("is-visible");
      // eslint-disable-next-line no-unused-expressions
      path.getBoundingClientRect();
      path.classList.add("is-visible");
      requestAnimationFrame(function () {
        path.style.strokeDashoffset = 0;
      });
    } else {
      path.style.strokeDasharray = "none";
      path.style.strokeDashoffset = 0;
      path.classList.add("is-visible");
    }

    dot.classList.add("is-visible");
  }

  function positionTooltip(id) {
  var target = targets[id];
  if (!target) return;
  var sRect = stageRect();
  var tRect = target.getBoundingClientRect();
  var copy = COPY[id];

  tooltip.innerHTML = copy
    ? '<span class="mp-tooltip-title">' + copy.title + '</span>' +
      (copy.sub ? '<span class="mp-tooltip-sub">' + copy.sub + '</span>' : '')
    : "";

  var dotX = tRect.left + tRect.width / 2 - sRect.left;
  var dotY = tRect.top + tRect.height / 2 - sRect.top;

  // Position tooltip above the row first so we can measure its real size
  tooltip.style.left = dotX + "px";
  tooltip.style.top  = dotY + "px";
  tooltip.classList.add("is-visible");

  dot.style.left = dotX + "px";
  dot.style.top  = dotY + "px";
  dot.classList.add("is-visible");

  requestAnimationFrame(function () {
    var ttRect = tooltip.getBoundingClientRect();
    var labelAnchorX = ttRect.left + ttRect.width / 2 - sRect.left;
    var labelAnchorY = ttRect.bottom - sRect.top;

    var d = "M" + dotX + "," + dotY + " L" + labelAnchorX + "," + labelAnchorY;
    path.setAttribute("d", d);

    if (!reduceMotion) {
      var len = path.getTotalLength();
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.classList.remove("is-visible");
      // eslint-disable-next-line no-unused-expressions
      path.getBoundingClientRect();
      path.classList.add("is-visible");
      requestAnimationFrame(function () {
        path.style.strokeDashoffset = 0;
      });
    } else {
      path.style.strokeDasharray = "none";
      path.style.strokeDashoffset = 0;
      path.classList.add("is-visible");
    }
  });
}

  function setLegendState(id) {
    Object.keys(legendItems).forEach(function (k) {
      legendItems[k].classList.toggle("is-active", k === id);
      legendItems[k].setAttribute("aria-pressed", k === id ? "true" : "false");
    });
  }

  function deactivateVisuals(id) {
  var label = labels[id];
  if (label) label.classList.remove("is-visible");
  var target = targets[id];
  if (target) target.classList.remove("mp-target-active");
  tooltip.classList.remove("is-visible");
  dot.classList.remove("is-visible");
  path.classList.remove("is-visible");
}

  function activate(id) {
    if (!targets[id]) return;
    if (active === id) return;
    if (active) deactivateVisuals(active);

    active = id;
    targets[id].classList.add("mp-target-active");
    setLegendState(id);

    if (isMobileSize) {
      positionTooltip(id);
    } else {
      positionLabel(id);
      var label = labels[id];
      if (label) label.classList.add("is-visible");
      requestAnimationFrame(function () { drawLine(id); });
    }
  }

  // ── Idle autoplay: cycles every hotspot until the user interacts ──────
  var AUTOPLAY_MS = 2400;
  var RESUME_AFTER_MS = 5000;
  var autoplayTimer = null;
  var resumeTimer = null;
  var autoplayIndex = 0;

  function autoplayStep() {
    activate(HOTSPOTS[autoplayIndex % HOTSPOTS.length]);
    autoplayIndex += 1;
  }

  function startAutoplay() {
    stopAutoplay();
    if (reduceMotion) return;
    autoplayStep();
    autoplayTimer = setInterval(autoplayStep, AUTOPLAY_MS);
  }
  function stopAutoplay() {
    if (autoplayTimer) { clearInterval(autoplayTimer); autoplayTimer = null; }
  }

  function onUserInteract(id) {
    stopAutoplay();
    if (id) activate(id);
    clearTimeout(resumeTimer);
    resumeTimer = setTimeout(startAutoplay, RESUME_AFTER_MS);
  }

  // ── Wiring ──────────────────────────────────────────────────────────
  // Phone elements are the primary surface: hover (desktop) / tap (touch)
  // on the phone itself drives activation first.
  HOTSPOTS.forEach(function (id) {
    var target = targets[id];
    var legendItem = legendItems[id];

    if (target) {
      if (!isTouch) {
        target.addEventListener("mouseenter", function () { onUserInteract(id); });
      }
      target.addEventListener("click", function (e) {
        e.stopPropagation();
        onUserInteract(id);
      });
      target.addEventListener("focus", function () { onUserInteract(id); });
    }

    if (legendItem) {
      if (!isTouch) {
        legendItem.addEventListener("mouseenter", function () { onUserInteract(id); });
      }
      legendItem.addEventListener("click", function () { onUserInteract(id); });
      legendItem.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onUserInteract(id); }
      });
    }
  });

  if (!isTouch) {
    stage.addEventListener("mouseleave", function () {
      // Don't fully deactivate — resume the idle cycle so the showcase
      // keeps demonstrating itself once the pointer moves away.
      clearTimeout(resumeTimer);
      resumeTimer = setTimeout(startAutoplay, 600);
    });
  }

  // Pause autoplay the instant the user touches the stage on mobile too.
  stage.addEventListener("touchstart", function () { stopAutoplay(); }, { passive: true });

  // ── Resize: throttled, recomputes geometry only (no layout thrash) ───
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      isMobileSize = window.matchMedia("(max-width: 640px)").matches;
      if (active) {
        if (isMobileSize) { positionTooltip(active); }
        else { positionLabel(active); drawLine(active); }
      }
    }, 120);
  }, { passive: true });

  // ── First-load progress bar animation ─────────────────────────────────
  var barFill = document.getElementById("mcBarFill");
  if (barFill) {
    var barTarget = barFill.dataset.fill || "0";
    if ("IntersectionObserver" in window) {
      var barObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            requestAnimationFrame(function () { barFill.style.width = barTarget + "%"; });
            barObserver.unobserve(e.target);
          }
        });
      }, { threshold: 0.4 });
      barObserver.observe(barFill);
    } else {
      barFill.style.width = barTarget + "%";
    }
  }

  // Kick off the idle demo so the interaction pattern is discoverable
  // before the user does anything.
  startAutoplay();
})();