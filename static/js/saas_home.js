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