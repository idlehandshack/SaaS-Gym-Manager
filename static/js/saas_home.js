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
  var tabs   = Array.from(document.querySelectorAll(".s5-tabs .s5-tab"));
  var panels = Array.from(document.querySelectorAll(".s5-panel"));
  if (!tabs.length) return;

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t)   { t.classList.remove("is-active"); });
      panels.forEach(function (p) { p.classList.remove("is-active"); });

      tab.classList.add("is-active");

      var target = document.getElementById(tab.dataset.panel);
      if (target) target.classList.add("is-active");
    });
  });
})();

/* ═══════════════════════════════════════════════════════════════
   PREMIUM SCREENSHOT CAROUSEL — vanilla JS, no dependencies
   Handles: infinite loop, autoplay, arrows, dots, keyboard,
   touch swipe, mouse drag, pause-on-hover.
   ═══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {
  'use strict';

  const root = document.getElementById('swc-browser');
  if (!root) return; // bail safely if markup isn't present on this page

  const viewport = document.getElementById('swc-viewport');
  const track    = document.getElementById('swc-track');
  const slides   = Array.from(track.querySelectorAll('.swc-slide'));
  const prevBtn  = document.getElementById('swc-prev');
  const nextBtn  = document.getElementById('swc-next');
  const dotsWrap = document.getElementById('swc-dots');
  const total    = slides.length;

  if (total === 0) return;

  let current = 0;
  let autoplayId = null;
  const AUTOPLAY_MS = 4000;

  const dotEls = slides.map((_, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'swc-dot-btn';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-label', 'Go to screenshot ' + (i + 1));
    btn.addEventListener('click', () => goTo(i, true));
    dotsWrap.appendChild(btn);
    return btn;
  });

  function render() {
    slides.forEach((slide, i) => {
      slide.classList.remove('is-active', 'is-prev', 'is-next', 'is-hidden');
      if (i === current) slide.classList.add('is-active');
      else if (i === mod(current - 1)) slide.classList.add('is-prev');
      else if (i === mod(current + 1)) slide.classList.add('is-next');
      else slide.classList.add('is-hidden');
    });
    dotEls.forEach((dot, i) => {
      dot.classList.toggle('is-active', i === current);
      dot.setAttribute('aria-selected', i === current ? 'true' : 'false');
    });
  }

  function mod(n) { return ((n % total) + total) % total; }

  function goTo(index, userTriggered) {
    current = mod(index);
    render();
    if (userTriggered) restartAutoplay();
  }

  function next(userTriggered) { goTo(current + 1, userTriggered); }
  function prev(userTriggered) { goTo(current - 1, userTriggered); }

  function startAutoplay() {
    stopAutoplay();
    autoplayId = setInterval(() => next(false), AUTOPLAY_MS);
  }
  function stopAutoplay() {
    if (autoplayId) { clearInterval(autoplayId); autoplayId = null; }
  }
  function restartAutoplay() { stopAutoplay(); startAutoplay(); }

  root.addEventListener('mouseenter', stopAutoplay);
  root.addEventListener('mouseleave', startAutoplay);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopAutoplay(); else startAutoplay();
  });

  prevBtn.addEventListener('click', () => prev(true));
  nextBtn.addEventListener('click', () => next(true));

  root.setAttribute('tabindex', '0');
  root.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(true); }
    if (e.key === 'ArrowRight') { e.preventDefault(); next(true); }
  });

  let pointerDown = false;
  let startX = 0;
  let deltaX = 0;
  const SWIPE_THRESHOLD = 50;

  function onPointerDown(x) {
    pointerDown = true;
    startX = x;
    deltaX = 0;
    root.classList.add('is-dragging');
    stopAutoplay();
  }
  function onPointerMove(x) {
    if (!pointerDown) return;
    deltaX = x - startX;
  }
  function onPointerUp() {
    if (!pointerDown) return;
    pointerDown = false;
    root.classList.remove('is-dragging');
    if (deltaX > SWIPE_THRESHOLD) prev(true);
    else if (deltaX < -SWIPE_THRESHOLD) next(true);
    else startAutoplay();
    deltaX = 0;
  }

  viewport.addEventListener('touchstart', (e) => onPointerDown(e.touches[0].clientX), { passive: true });
  viewport.addEventListener('touchmove',  (e) => onPointerMove(e.touches[0].clientX),  { passive: true });
  viewport.addEventListener('touchend', onPointerUp);

  viewport.addEventListener('mousedown', (e) => { e.preventDefault(); onPointerDown(e.clientX); });
  window.addEventListener('mousemove', (e) => onPointerMove(e.clientX));
  window.addEventListener('mouseup', onPointerUp);

  render();
  startAutoplay();
});

/* -----------------------------------------------------------------------------
   6. PLAN CALCULATOR
----------------------------------------------------------------------------- */
(function () {

  var PAYG_TIERS = [
    { upTo: 100,      rate: 6.99 },
    { upTo: 300,      rate: 5.99 },
    { upTo: Infinity, rate: 4.99 },
  ];

  var FIXED_PLANS = {
    3:  { base: 3499, extraBranch: 1750 },
    6:  { base: 5999, extraBranch: 3000 },
    12: { base: 9999, extraBranch: 5000 },
  };

  function paygRateFor(members) {
    for (var i = 0; i < PAYG_TIERS.length; i++) {
      if (members <= PAYG_TIERS[i].upTo) return PAYG_TIERS[i].rate;
    }
    return PAYG_TIERS[PAYG_TIERS.length - 1].rate;
  }

  function costRow(label, value) {
    return (
      '<div class="s6-cost-row">' +
        '<span class="s6-cost-label">' + label + "</span>" +
        '<span class="s6-cost-val">'   + value + "</span>" +
      "</div>"
    );
  }

  function calcFixed(months, branches) {
    var plan    = FIXED_PLANS[months];
    var extra   = branches > 1 ? (branches - 1) * plan.extraBranch : 0;
    var total   = plan.base + extra;
    var monthly = total / months;

    var html = costRow(months + "-month base (1 branch)", fmtRupee(plan.base));

    if (extra > 0) {
      var addlLabel = (branches - 1) + " additional branch" + (branches - 1 > 1 ? "es" : "");
      html += costRow(addlLabel, fmtRupee(extra));
    }

    html += costRow("Effective monthly cost", fmtRupee(monthly) + "/mo");

    return { html: html, total: total, monthly: monthly };
  }

  var branchSlider  = document.getElementById("calc-branches");
  var branchDisplay = document.getElementById("calc-branches-display");
  var branchesGrid  = document.getElementById("calc-branches-grid");
  var runBtn        = document.getElementById("calc-run-btn");

  if (!branchSlider || !runBtn) return;

  function rebuildBranchInputs(count) {
    var existing = Array.from(branchesGrid.querySelectorAll(".s6-branch-input"));
    var saved    = existing.map(function (i) { return i.value; });

    branchesGrid.innerHTML = "";

    for (var i = 0; i < count; i++) {
      var row   = document.createElement("div");
      row.className = "s6-branch-row";

      var label = document.createElement("span");
      label.className   = "s6-branch-label";
      label.textContent = "Branch " + (i + 1);

      var input = document.createElement("input");
      input.className   = "s6-branch-input";
      input.type        = "number";
      input.min         = "1";
      input.max         = "9999";
      input.placeholder = "Active members";
      input.setAttribute("data-branch", i);
      input.value       = saved[i] || "";

      row.appendChild(label);
      row.appendChild(input);
      branchesGrid.appendChild(row);
    }
  }

  branchSlider.addEventListener("input", function () {
    var val = parseInt(this.value, 10);
    branchDisplay.textContent = val >= 10 ? "10+" : val;
    rebuildBranchInputs(val);
  });

  var planTabs    = Array.from(document.querySelectorAll(".s6-plan-tab"));
  var planDetails = Array.from(document.querySelectorAll(".s6-plan-detail"));

  function activatePlanTab(key) {
    planTabs.forEach(function (t) {
      t.classList.toggle("is-active", t.dataset.plan === key);
    });
    planDetails.forEach(function (d) {
      d.classList.toggle("is-active", d.id === "detail-" + key);
    });
  }

  planTabs.forEach(function (tab) {
    tab.addEventListener("click", function () { activatePlanTab(this.dataset.plan); });
  });

  runBtn.addEventListener("click", function () {
    var gymName      = (document.getElementById("calc-gym-name").value.trim()) || "Your Gym";
    var branches     = parseInt(branchSlider.value, 10);
    var memberInputs = Array.from(branchesGrid.querySelectorAll(".s6-branch-input"));

    var branchMembers = memberInputs.map(function (inp) {
      return parseInt(inp.value, 10) || 0;
    });

    var totalMembers = branchMembers.reduce(function (a, b) { return a + b; }, 0);

    if (totalMembers === 0) {
      if (memberInputs[0]) memberInputs[0].focus();
      return;
    }

    var paygMonthly  = 0;
    var paygRowsHTML = "";

    branchMembers.forEach(function (m, i) {
      if (m === 0) return;
      var rate = paygRateFor(m);
      var cost = m * rate;
      paygMonthly += cost;
      paygRowsHTML += costRow(
        "Branch " + (i + 1) + " — " + m.toLocaleString("en-IN") + " members @ ₹" + rate,
        fmtRupee(cost) + "/mo"
      );
    });

    document.getElementById("payg-rows").innerHTML    = paygRowsHTML;
    document.getElementById("payg-total").textContent = fmtRupee(paygMonthly) + "/mo";

    var f3  = calcFixed(3,  branches);
    var f6  = calcFixed(6,  branches);
    var f12 = calcFixed(12, branches);

    document.getElementById("fixed3-rows").innerHTML     = f3.html;
    document.getElementById("fixed3-total").textContent  = fmtRupee(f3.total);
    document.getElementById("fixed6-rows").innerHTML     = f6.html;
    document.getElementById("fixed6-total").textContent  = fmtRupee(f6.total);
    document.getElementById("fixed12-rows").innerHTML    = f12.html;
    document.getElementById("fixed12-total").textContent = fmtRupee(f12.total);

    var paygAnnual = paygMonthly * 12;
    var bestPlan   = "payg";

    if (paygAnnual > f12.total && totalMembers >= 50) bestPlan = "fixed12";
    else if (paygMonthly > f6.monthly)                bestPlan = "fixed6";

    var recommendLabels = {
      payg:    "Pay-as-you-Go is most flexible for your size",
      fixed6:  "6-Month plan offers the best value right now",
      fixed12: "12-Month plan saves you the most annually",
    };

    activatePlanTab(bestPlan);
    document.getElementById("calc-recommend-text").textContent = recommendLabels[bestPlan];

    var savingsHTML = [
      { label: "PAYG / mo",  val: fmtRupee(paygMonthly), green: false },
      { label: "6-mo / mo",  val: fmtRupee(f6.monthly),  green: f6.monthly  < paygMonthly },
      { label: "12-mo / mo", val: fmtRupee(f12.monthly), green: true },
    ].map(function (item) {
      return (
        '<div class="s6-savings-item">' +
          '<div class="s6-savings-num' + (item.green ? " green" : "") + '">' + item.val + "</div>" +
          '<div class="s6-savings-sub">' + item.label + "</div>"  +
        "</div>"
      );
    }).join("");

    document.getElementById("savings-grid").innerHTML = savingsHTML;

    document.getElementById("calc-gym-display").textContent = gymName;
    document.getElementById("calc-result-sub").textContent  =
      branches + " branch" + (branches > 1 ? "es" : "") +
      " · " + totalMembers.toLocaleString("en-IN") + " total members";

    document.getElementById("calc-empty").style.display = "none";
    document.getElementById("calc-result").classList.add("is-visible");
    document.getElementById("calc-savings").classList.add("is-visible");
    document.getElementById("calc-cta").classList.add("is-visible");
  });

})();


/* -----------------------------------------------------------------------------
   7. TESTIMONIALS CAROUSEL
----------------------------------------------------------------------------- */
(function () {
  var carousel = document.getElementById("testiCarousel");
  var stage    = document.getElementById("testiStage");
  var prevBtn  = document.getElementById("testiPrev");
  var nextBtn  = document.getElementById("testiNext");
  var dotsWrap = document.getElementById("testiDots");
  if (!carousel || !stage) return;

  var cards = Array.from(stage.children);
  if (!cards.length) return;

  var AUTOPLAY_MS   = 5000;
  var autoplayTimer = null;
  var current       = cards.findIndex(function (c) { return c.classList.contains("is-active"); });
  if (current < 0) current = 0;

  function buildDots() {
    if (!dotsWrap) return;
    dotsWrap.innerHTML = "";
    cards.forEach(function (_, i) {
      var dot = document.createElement("button");
      /* FIX: reset browser-default button styles so dots render as clean
         circles. Without this, browser UA stylesheet padding/border
         distorts the width/height set by .testi-dot in CSS. */
      dot.style.padding    = "0";
      dot.style.border     = "none";
      dot.style.background = "transparent";
      dot.style.cursor     = "pointer";
      dot.className = "testi-dot";
      dot.setAttribute("aria-label", "Go to testimonial " + (i + 1));
      dot.addEventListener("click", function () { goTo(i); restartAutoplay(); });
      dotsWrap.appendChild(dot);
    });
    updateDots();
  }

  function updateDots() {
    if (!dotsWrap) return;
    Array.from(dotsWrap.children).forEach(function (d, i) {
      d.classList.toggle("is-active", i === current);
    });
  }

  function goTo(index) {
    var next = ((index % cards.length) + cards.length) % cards.length;
    if (next === current) return;
    cards[current].classList.remove("is-active");
    cards[next].classList.add("is-active");
    current = next;
    updateDots();
  }

  function goNext() { goTo(current + 1); }
  function goPrev() { goTo(current - 1); }

  if (prevBtn) prevBtn.addEventListener("click", function () { goPrev(); restartAutoplay(); });
  if (nextBtn) nextBtn.addEventListener("click", function () { goNext(); restartAutoplay(); });

  function startAutoplay()   { stopAutoplay(); autoplayTimer = setInterval(goNext, AUTOPLAY_MS); }
  function stopAutoplay()    { if (autoplayTimer) { clearInterval(autoplayTimer); autoplayTimer = null; } }
  function restartAutoplay() { stopAutoplay(); startAutoplay(); }

  carousel.addEventListener("mouseenter", stopAutoplay);
  carousel.addEventListener("mouseleave", startAutoplay);
  carousel.addEventListener("touchstart",  stopAutoplay,  { passive: true });
  carousel.addEventListener("focusin",     stopAutoplay);
  carousel.addEventListener("focusout",    startAutoplay);

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) startAutoplay();
        else stopAutoplay();
      });
    }, { threshold: 0.2 }).observe(carousel);
  } else {
    startAutoplay();
  }

  buildDots();
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