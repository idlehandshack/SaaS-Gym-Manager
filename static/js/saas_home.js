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
/* -----------------------------------------------------------------------------
   11. MEMBER / OWNER JOURNEY — tabbed, click-to-start, in-place, video-driven
----------------------------------------------------------------------------- */
(function () {
  var section = document.getElementById('member-journey');
  var stage = document.getElementById('mjStage');
  var tabsWrap = document.getElementById('mjTabs');
  if (!section || !stage || !tabsWrap) return;

  var HAS_GSAP = typeof window.gsap !== 'undefined';
  if (!HAS_GSAP) return;

  var V = window.STATIC_URL ? window.STATIC_URL + 'videos/journey/' : '/static/videos/journey/';

  var viewport      = document.getElementById('mjSceneViewport');
  var progFill      = document.getElementById('mjProgFill');
  var progLabel     = document.getElementById('mjProgLabel');
  var miniTimeline  = document.getElementById('mjMiniTimeline');
  var finalScene    = document.getElementById('mjFinalScene');
  var finalTimeline = document.getElementById('mjFinalTimeline');
  var startOverlay  = document.getElementById('mjStartOverlay');
  var startBtn      = document.getElementById('mjStartBtn');
  var exploreBtn    = document.getElementById('mjExploreBtn');

  var tabBtns = Array.prototype.slice.call(tabsWrap.querySelectorAll('.mj-tab'));
  var tabIndicator = document.getElementById('mjTabIndicator');

  /* ---- Per-journey config. Swap in real video files/labels/urls. ---- */
  var JOURNEYS = {
    member: {
      labels:   ['Create Account', 'Enroll into Gym', 'Enable Notifications', 'Enable Location', 'Profile Activated'],
      videos:   ['01-signup.mp4', '02-enrollment.mp4', '03-notifications.mp4', '04-location.mp4', '05-profile.mp4'],
      urls:     ['golden-gym.entergym.in/signup/', 'golden-gym.entergym.in/enrollment/', 'golden-gym.entergym.in/profile/', 'golden-gym.entergym.in/attendence/', 'golden-gym.entergym.in/profile/'],
      eyebrows: ['Step 01 — Getting Started', 'Step 02 — Membership', 'Step 03 — Stay Informed', 'Step 04 — Frictionless Check-in', 'Step 05 — All Set'],
      finalTitle: 'Your Member Journey Starts Here.',
      finalBody: 'From first sign-up to walking through the door — every step of the EnterGYM member experience, connected into one seamless flow.',
      startLabel: 'Start member journey'
    },
    owner: {
      labels:   ['Login into Gym Account', 'Set Up Plans and Trainer', 'Add Members', 'Update Payments', 'Dashboard Live'],
      videos:   ['o1-login.mp4', 'o2-plans.mp4', 'o3-staff.mp4', 'o4-payments.mp4', 'o5-dashboard.mp4'],
      urls:     ['golden-gym.entergym.in/login/', 'golden-gym.entergym.in/plans/trainer/', 'golden-gym.entergym.in/quickenrollment/', 'golden-gym.entergym.in/payments/', 'golden-gym.entergym.in/dashboard/'],
      eyebrows: ['Step 01 — Get Started', 'Step 02 — Pricing', 'Step 03 — Your Team', 'Step 04 — Revenue', 'Step 05 — Live'],
      finalTitle: 'Run Your Gym From One Dashboard.',
      finalBody: 'From onboarding to daily operations — everything a gym owner needs to manage members, staff, and revenue in one place.',
      startLabel: 'Start gym owner journey'
    }
  };

  var SCROLL_LOCK_MS = 3000; // no scroll navigation for the first 3s of a step

  var activeKey = stage.dataset.journey || 'member';
  var scenes = [], mockups = [], videos = [], miniDots = [];
  var gen = 0;
  var current = 0;
  var animating = false;
  var stepStartedAt = 0;

  // The single in-flight playback attempt (if any). Lets stopAllVideos()/
  // resetAll() forcibly retire a pending playStep() Promise — pausing the
  // video, stripping its listeners, and resolving it as a no-op — instead
  // of leaving it dangling with nothing left to ever settle it.
  var currentPlayback = null;

  // Set for the brief window while the Start-button's own smooth
  // scrollIntoView() is settling. The intentional scroll can itself cause
  // the stage to momentarily read as "not intersecting" — without this
  // guard, the IntersectionObserver below would immediately resetAll() the
  // journey we just started. Genuinely leaving the section (user scrolls
  // away later) still resets normally once this flag is clear.
  var suppressIntersectionReset = false;

  function bar(url) {
    return '<div class="mj-mockup-bar"><span class="mj-mdot r"></span><span class="mj-mdot y"></span><span class="mj-mdot g"></span><span class="mj-murl">' + url + '</span></div>';
  }

  /* ---- video lifecycle state helpers ----
     States: idle -> loading -> ready -> playing -> (failed)
     "ended" doesn't get its own persistent state; a naturally-completed
     video is left as "ready" so it can be replayed (e.g. goBackward) without
     re-downloading. Stored on data-video-state so it's inspectable in
     DevTools during manual QA. */
  function getVState(video) { return video ? (video.dataset.videoState || 'idle') : 'idle'; }
  function setVState(video, state) { if (video) video.dataset.videoState = state; }

  /* Tears down and rebuilds the mini-timeline, final-timeline, and scene
     DOM for whichever journey key is passed in. Videos get a data-src
     placeholder only — no real src is assigned until loadVideo() runs
     for that specific step, so nothing is requested on build/tab-switch. */
  function buildJourney(key) {
    var cfg = JOURNEYS[key];
    if (!cfg) return;

    viewport.innerHTML = '';
    miniTimeline.innerHTML = '';
    finalTimeline.innerHTML = '';
    if (finalScene.querySelector('h2')) finalScene.querySelector('h2').textContent = cfg.finalTitle;
    if (finalScene.querySelector('p')) finalScene.querySelector('p').textContent = cfg.finalBody;

    scenes = []; mockups = []; videos = []; miniDots = [];

    cfg.labels.forEach(function (label, i) {
      var wrap = document.createElement('div');
      wrap.className = 'mj-mini-dot-wrap';
      wrap.innerHTML = '<div class="mj-mini-dot" data-i="' + (i + 1) + '"><div class="mj-core"></div></div>';
      miniTimeline.appendChild(wrap);
      miniDots.push(wrap.querySelector('.mj-mini-dot'));
      if (i < cfg.labels.length - 1) {
        var line = document.createElement('div');
        line.className = 'mj-mini-line';
        line.dataset.i = i + 1;
        line.innerHTML = '<div class="mj-fill"></div><div class="mj-particle"></div>';
        miniTimeline.appendChild(line);
      }
    });

    cfg.labels.forEach(function (_, i) {
      var d = document.createElement('div');
      d.className = 'mj-ft-dot';
      d.dataset.n = String(i + 1).padStart(2, '0');
      finalTimeline.appendChild(d);
      if (i < cfg.labels.length - 1) {
        var l = document.createElement('div');
        l.className = 'mj-ft-line';
        l.innerHTML = '<div class="mj-p"></div>';
        finalTimeline.appendChild(l);
      }
    });

    cfg.labels.forEach(function (_, idx) {
      var sc = document.createElement('div');
      sc.className = 'mj-scene';
      sc.id = 'mjScene' + (idx + 1);
      sc.innerHTML =
        '<div class="mj-mockup-wrap">' +
          '<div class="mj-mockup-topbar">' +
            '<button class="mj-skip-btn" type="button">Skip step <span>\u2192</span></button>' +
            '<button class="mj-close-btn" type="button" aria-label="Close journey">\u2715</button>' +
          '</div>' +
          '<div class="mj-mockup">' +
            bar(cfg.urls[idx]) +
            '<div class="mj-mscreen">' +
              '<video playsinline muted preload="metadata" data-src="' + V + cfg.videos[idx] + '" data-video-state="idle"></video>' +
            '</div>' +
          '</div>' +
          '<div class="mj-caption"><div class="mj-eyebrow">' + cfg.eyebrows[idx] + '</div><h3>' + cfg.labels[idx] + '</h3></div>' +
        '</div>';
      viewport.appendChild(sc);
      scenes.push(sc);
      mockups.push(sc.querySelector('.mj-mockup'));
      videos.push(sc.querySelector('video'));

      var skipBtn = sc.querySelector('.mj-skip-btn');
      skipBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        goForward();
      });

      var closeBtnEl = sc.querySelector('.mj-close-btn');
      closeBtnEl.addEventListener('click', function (e) {
        e.stopPropagation();
        closeJourney();
      });
    });
  }

  function delay(sec) { return new Promise(function (res) { setTimeout(res, sec * 1000); }); }

  function markStepStart() { stepStartedAt = Date.now(); }
  function scrollLocked() { return (Date.now() - stepStartedAt) < SCROLL_LOCK_MS; }

  // Pauses every video that has actually been loaded (never forces an
  // unloaded one to load just to pause it), and forcibly retires whatever
  // playback attempt is currently in flight so it can never advance the
  // journey later — this is the single choke point every interruption
  // (skip, scroll, close, tab switch, leaving the viewport) routes through.
  // Loaded videos are never stripped of their src here — a previously
  // downloaded video stays downloaded and can be resumed/replayed later.
  function stopAllVideos() {
    videos.forEach(function (v) {
      if (!v) return;
      var st = getVState(v);
      if (st === 'ready' || st === 'playing' || st === 'loading') {
        try { v.pause(); } catch (e) { /* ignore — element may be mid-teardown */ }
        if (st === 'playing') setVState(v, 'ready');
      }
    });
    if (currentPlayback) {
      var cp = currentPlayback;
      currentPlayback = null;
      cp.cancel();
    }
  }

  // Assigns the real src for step n's video, idempotently:
  //   - idle    -> assign data-src to src exactly once, call load(), mark loading
  //   - loading -> a load is already in flight; reuse the same element/attempt
  //   - ready / playing -> already successfully loaded; reuse as-is
  //   - failed  -> allow a controlled retry (caller explicitly re-entered this step)
  // Loaded state is tracked via data-video-state rather than inferring it
  // from video.src, so a video already downloaded is always reused, never
  // re-requested — and a failed attempt is never mistaken for a success.
  function loadVideo(n) {
    var video = videos[n - 1];
    if (!video) return null;

    var state = getVState(video);
    if (state === 'ready' || state === 'playing' || state === 'loading') return video;

    // idle or failed: (re-)assign data-src to src exactly once and kick off
    // load(). No listeners of any kind are attached here — playStep() owns
    // the entire loading-to-playback lifecycle for the active video, so
    // there's a single source of truth and no race between two listeners.
    var src = video.dataset.src;
    if (!src) return video;

    console.log('[member-journey] loading step ' + n);
    setVState(video, 'loading');
    video.src = src;
    video.load();
    return video;
  }

  function revealMockup(mockupEl) {
    if (!mockupEl) return Promise.resolve();
    gsap.killTweensOf(mockupEl);
    return new Promise(function (resolve) {
      gsap.fromTo(mockupEl,
        { opacity: 0, scale: 0.94 },
        { opacity: 1, scale: 1, duration: 0.5, ease: 'power3.out', clearProps: 'transform', onComplete: resolve }
      );
    });
  }

  // Event-driven playback for step n. Resolves in exactly two situations:
  //   1) the video's native `ended` event fires (normal completion), or
  //   2) this attempt is explicitly cancelled from outside (stopAllVideos(),
  //      called by enterStep/resetAll/goToFinal on skip, scroll, close, or
  //      tab switch) — resolved as a no-op; the caller's generation check
  //      (`myGen !== gen`) is what actually prevents any advance.
  // A failed load, a playback error, or a rejected play() Promise are all
  // logged and left "stuck" on the current scene on purpose — per spec, a
  // failure must never silently advance the journey. The user can still
  // move on via Skip/scroll, which cancels this attempt through the path above.
  // playStep() always settles exactly once, with an explicit result object:
  //   { status: 'ended' }     — video reached its native `ended` event
  //   { status: 'cancelled' } — explicitly interrupted (Skip/scroll/Close/
  //                             tab switch/reset/newer generation)
  //   { status: 'failed' }    — load error, media error, aborted load, or
  //                             a rejected play() Promise
  // A `settled` flag guards the single internal finish() so late events
  // (an `ended` that fires after cancel, an `error` after ended, a
  // play()-rejection after cancel, etc.) can never settle it twice or flip
  // an already-decided result.
  // playStep() is the SOLE owner of the active video's loading-to-playback
  // lifecycle: it calls loadVideo() (which only assigns src/load()s and
  // never attaches listeners), then inspects video.readyState directly
  // instead of blindly waiting for loadeddata/canplay — a video can already
  // be at readyState >= 2 by the time we get here (e.g. it finished loading
  // during the mockup-reveal animation), and waiting on an event that has
  // already fired is exactly what previously left playback stuck after a
  // successful download.
  function playStep(n, myGen) {
    var video = loadVideo(n);
    if (!video) return Promise.resolve({ status: 'failed' });

    return new Promise(function (resolve) {
      var settled = false;
      var cleanedUp = false;
      var playbackStarted = false;    // guards video.play() so it fires at most once
      var cancelRequested = false;    // distinguishes an intentional cancel from a real abort/error
      var attemptToken = {};

      function cleanupListeners() {
        if (cleanedUp) return;
        cleanedUp = true;
        video.removeEventListener('loadeddata', onReady);
        video.removeEventListener('canplay', onReady);
        video.removeEventListener('ended', onEnded);
        video.removeEventListener('error', onError);
        video.removeEventListener('abort', onAbort);
      }

      function clearOwnPlayback() {
        if (currentPlayback && currentPlayback.video === video && currentPlayback.__attempt === attemptToken) {
          currentPlayback = null;
        }
      }

      function finish(status) {
        if (settled) return;
        settled = true;
        cleanupListeners();
        clearOwnPlayback();
        resolve({ status: status });
      }

      function logFailure(reason) {
        var src = video.dataset.src || video.currentSrc || video.src || '(unknown source)';
        console.warn('[member-journey] video failed for step ' + n + ': ' + src + (reason ? ' — ' + reason : ''));
      }

      function onReady() {
        // Only relevant while we're still waiting to start playback; once
        // attemptPlay() has run once, later loadeddata/canplay firings are
        // ignored (playbackStarted guards attemptPlay itself, too).
        if (settled || myGen !== gen) return;
        attemptPlay();
      }

      function onEnded() {
        // Natural completion — leave state as "ready" so a later replay
        // (goBackward -> goForward again) doesn't need to re-download.
        if (getVState(video) === 'playing') setVState(video, 'ready');
        console.log('[member-journey] ended step ' + n);
        finish('ended');
      }

      function onError() {
        setVState(video, 'failed');
        logFailure('media error event');
        finish('failed');
      }

      function onAbort() {
        // Fires both on an intentional cancel-triggered pause/reload and on
        // a genuine media/network abort. If we already know this is an
        // intentional cancellation (cancelRequested), let cancel()'s own
        // finish('cancelled') be the one settlement — do nothing here.
        if (cancelRequested) return;
        setVState(video, 'failed');
        logFailure('aborted');
        finish('failed');
      }

      video.addEventListener('ended', onEnded);
      video.addEventListener('error', onError);
      video.addEventListener('abort', onAbort);

      currentPlayback = {
        video: video,
        __attempt: attemptToken,
        cancel: function () {
          if (settled) return;
          cancelRequested = true;
          try { video.pause(); } catch (e) { /* ignore — element may be mid-teardown */ }
          if (getVState(video) === 'playing') setVState(video, 'ready');
          finish('cancelled');
        }
      };

      function attemptPlay() {
        if (settled || playbackStarted) return;
        playbackStarted = true;
        video.removeEventListener('loadeddata', onReady);
        video.removeEventListener('canplay', onReady);

        console.log('[member-journey] attempting play step ' + n);
        video.currentTime = 0;
        var playPromise = video.play();
        setVState(video, 'playing');
        if (playPromise && playPromise.then) {
          playPromise.then(function () {
            console.log('[member-journey] play() resolved step ' + n);
          }, function (err) {
            if (settled) return; // already cancelled/ended — ignore late rejection
            console.warn('[member-journey] play failed step ' + n);
            setVState(video, 'failed');
            logFailure('play() rejected: ' + (err && err.message ? err.message : err));
            finish('failed');
          });
        }
      }

      // readyState >= 2 (HAVE_CURRENT_DATA) means the browser already has
      // enough of the current frame to start playback — check this FIRST,
      // synchronously, before ever attaching a readiness listener. This is
      // what closes the race: the video may have finished loading while we
      // were off doing the mockup-reveal GSAP animation, in which case
      // loadeddata/canplay already fired and nothing would ever call us again.
      console.log('[member-journey] readyState: ' + video.readyState + ' for step ' + n);
      if (video.readyState >= 2) {
        attemptPlay();
      } else {
        var state = getVState(video);
        if (state === 'idle' || state === 'failed') {
          // Defensive: loadVideo() should already have moved this to
          // "loading" and called load(), but if we somehow got here with
          // nothing in flight, there is no readiness event coming.
          logFailure('no playable video for this attempt (state: ' + state + ')');
          setVState(video, 'failed');
          finish('failed');
          return;
        }
        video.addEventListener('loadeddata', onReady);
        video.addEventListener('canplay', onReady);
      }
    });
  }

  function setProgress(step) {
    var total = JOURNEYS[activeKey].labels.length;
    var pct = Math.min(step, total) * (100 / total);
    gsap.to(progFill, { width: pct + '%', duration: 0.5, ease: 'power2.out' });
    progLabel.textContent = (step >= 1 && step <= total)
      ? ('Step ' + step + ' of ' + total)
      : (step === total + 1 ? 'Complete' : ('Step 1 of ' + total));
    miniDots.forEach(function (d, idx) {
      var i = idx + 1;
      d.classList.toggle('done', i < step);
      d.classList.toggle('now', i === step);
    });
    miniTimeline.querySelectorAll('.mj-mini-line').forEach(function (l) {
      var i = +l.dataset.i;
      gsap.to(l.querySelector('.mj-fill'), { height: i < step ? '100%' : '0%', duration: 0.5, ease: 'power2.out' });
      l.classList.toggle('flow', i === step);
    });
  }

  function showScene(idx) {
    scenes.forEach(function (sc, i) { sc.classList.toggle('active', i === idx - 1); });
  }

  function resetFinalVisuals() {
    finalScene.classList.remove('active');
    finalTimeline.querySelectorAll('.mj-ft-dot').forEach(function (d) { gsap.set(d, { opacity: 0, scale: .3 }); });
    if (finalScene.querySelector('h2')) gsap.set(finalScene.querySelector('h2'), { opacity: 0, y: 16 });
    if (finalScene.querySelector('p')) gsap.set(finalScene.querySelector('p'), { opacity: 0, y: 16 });
    if (finalScene.querySelector('.mj-cta-btn')) gsap.set(finalScene.querySelector('.mj-cta-btn'), { opacity: 0, y: 16 });
  }

  // Safe to call any number of times in a row (tab switching calls it
  // twice back to back): stops/cancels playback, resets only videos that
  // were actually loaded (never forces a download), and re-idles the stage.
  function resetAll() {
    gen++;
    gsap.killTweensOf('#member-journey *');
    stopAllVideos();
    videos.forEach(function (v) {
      if (!v) return;
      var st = getVState(v);
      if (st === 'ready' || st === 'playing') {
        try { v.currentTime = 0; } catch (e) { /* ignore */ }
        setVState(v, 'ready');
      }
      // idle/loading/failed videos are left alone — never forced to load,
      // never have their src touched, just as before.
    });
    resetFinalVisuals();

    current = 0;
    animating = false;
    stage.classList.remove('mj-active');
    document.body.style.overflow = '';
    setProgress(1);
    showScene(1);
    if (mockups[0]) gsap.set(mockups[0], { opacity: 1 });
  }

  // Moves the stage forward into step `n`: flips the mockup into place,
  // then plays its video. If the video finishes on its own (i.e. nothing
  // else interrupted this generation), it advances again automatically.
  // Moves the stage forward into step `n`: kicks off the video's
  // load -> readyState-check -> play lifecycle immediately (so playback
  // starts as close to the user's gesture as possible), while the mockup's
  // GSAP reveal animation runs concurrently rather than gating it. The two
  // are independent: the visual reveal is purely cosmetic and must never
  // delay video.play(), and video.play() must never wait on GSAP.
  async function enterStep(n) {
    gen++;
    var myGen = gen;
    animating = true;
    stopAllVideos(); // retires whatever the previous step/generation was doing
    resetFinalVisuals();
    current = n;
    setProgress(n);
    showScene(n);
    markStepStart();

    var mockup = mockups[n - 1];
    if (mockup) gsap.set(mockup, { opacity: 0 });

    // Fire playback immediately — do NOT await the reveal animation first.
    var playbackPromise = playStep(n, myGen);
    revealMockup(mockup).then(function () {
      if (myGen === gen) animating = false;
    });

    var result = await playbackPromise;
    if (myGen !== gen) return; // interrupted/cancelled/tab-switched — never advance
    if (!result || result.status !== 'ended') return; // cancelled or failed — stay put
    goForward(); // video reached its natural `ended` event — move on
  }

  async function goToFinal() {
    gen++;
    var myGen = gen;
    animating = true;
    stopAllVideos();
    scenes.forEach(function (sc) { sc.classList.remove('active'); });
    var total = JOURNEYS[activeKey].labels.length;
    setProgress(total + 1);
    current = total + 1;

    await delay(0.2); if (myGen !== gen) return;
    finalScene.classList.add('active');
    var dots = finalTimeline.querySelectorAll('.mj-ft-dot');
    var tline = gsap.timeline();
    dots.forEach(function (d, i) { tline.to(d, { opacity: 1, scale: 1, duration: 0.4, ease: 'back.out(2)' }, i * 0.14); });
    tline.to(finalScene.querySelector('h2'), { opacity: 1, y: 0, duration: 0.5, ease: 'power3.out' }, '-=0.15')
         .to(finalScene.querySelector('p'), { opacity: 1, y: 0, duration: 0.5, ease: 'power3.out' }, '-=0.35')
         .to(finalScene.querySelector('.mj-cta-btn'), { opacity: 1, y: 0, duration: 0.5, ease: 'back.out(1.7)' }, '-=0.3');
    await new Promise(function (res) { tline.eventCallback('onComplete', res); });
    if (myGen !== gen) return;
    animating = false;
    markStepStart();
  }

  // Advances one step forward — used by: video-end, "Skip step", scroll-down.
  function goForward() {
    if (animating) return;
    var total = JOURNEYS[activeKey].labels.length;
    if (current >= 1 && current < total) enterStep(current + 1);
    else if (current === total) goToFinal();
    // current === total+1 (final): nothing further forward
  }

  // Goes back one step — used by: scroll-up only.
  function goBackward() {
    if (animating) return;
    var total = JOURNEYS[activeKey].labels.length;
    if (current === total + 1) enterStep(total);
    else if (current > 1) enterStep(current - 1);
    // current === 1: nothing before the first step
  }

  function closeJourney() { resetAll(); }

  /* Updates only the button's leading text node, leaving the arrow <svg> child intact. */
  function setStartLabel(key) {
    var label = (JOURNEYS[key] && JOURNEYS[key].startLabel) || 'Start journey';
    if (startBtn.childNodes.length && startBtn.childNodes[0].nodeType === Node.TEXT_NODE) {
      startBtn.childNodes[0].nodeValue = label + ' ';
    } else {
      startBtn.insertBefore(document.createTextNode(label + ' '), startBtn.firstChild);
    }
  }

  /* -------------------- tab switching -------------------- */
  function moveIndicator(btn) {
    if (!btn || !tabIndicator) return;
    tabIndicator.style.width = btn.offsetWidth + 'px';
    tabIndicator.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
  }

  function switchJourney(key) {
    if (key === activeKey || !JOURNEYS[key]) return;

    resetAll(); // stop/cancel current journey's playback, unlock scroll, clear state

    activeKey = key;
    stage.dataset.journey = key;

    buildJourney(key); // rebuild scenes/timeline for the new journey (no videos loaded yet)
    resetAll();        // set initial progress/scene for the fresh build
    setStartLabel(key);

    tabBtns.forEach(function (b) {
      var isActive = b.dataset.journey === key;
      b.classList.toggle('active', isActive);
      b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    var activeBtn = tabBtns.filter(function (b) { return b.dataset.journey === key; })[0];
    moveIndicator(activeBtn);
  }

  tabBtns.forEach(function (btn) {
    btn.addEventListener('click', function () { switchJourney(btn.dataset.journey); });
  });

  /* -------------------- entry -------------------- */
  startBtn.addEventListener('click', function () {
    stage.classList.add('mj-active');
    var target = window.matchMedia('(max-width: 820px)').matches ? section : stage;
    suppressIntersectionReset = true;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setTimeout(function () { document.body.style.overflow = 'hidden'; }, 650);
    // A smooth scrollIntoView typically settles well under a second; give
    // it a generous window before letting IntersectionObserver matter again.
    setTimeout(function () { suppressIntersectionReset = false; }, 1200);
    enterStep(1);
  });

  if (exploreBtn) exploreBtn.addEventListener('click', closeJourney);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && stage.classList.contains('mj-active')) closeJourney();
  });

  // Scroll-to-navigate: only active once a journey is underway, and only
  // while the cursor is over the stage itself (page scroll works normally
  // everywhere else). The first 3s of every step ignore scroll entirely;
  // after that, scrolling down moves forward and scrolling up moves back —
  // always overriding whatever else was happening (skip behaves the same way).
  stage.addEventListener('wheel', function (e) {
    if (!stage.classList.contains('mj-active') || current === 0) return;
    e.preventDefault();
    if (scrollLocked() || animating) return;
    if (e.deltaY > 0) goForward();
    else if (e.deltaY < 0) goBackward();
  }, { passive: false });

  /* Leaving the section resets it — always a fresh start on return. */
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting && !suppressIntersectionReset) resetAll();
      });
    }, { threshold: 0 }).observe(stage);
  }

  /* -------------------- init -------------------- */
  buildJourney(activeKey);
  resetAll();
  setStartLabel(activeKey);

  var initialBtn = tabBtns.filter(function (b) { return b.dataset.journey === activeKey; })[0];
  if (initialBtn) {
    initialBtn.classList.add('active');
    initialBtn.setAttribute('aria-selected', 'true');
    requestAnimationFrame(function () { moveIndicator(initialBtn); });
  }
  window.addEventListener('resize', function () {
    var b = tabBtns.filter(function (x) { return x.dataset.journey === activeKey; })[0];
    moveIndicator(b);
  });
})();