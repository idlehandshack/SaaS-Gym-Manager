/* =============================================================================
   EnterGYM — saas_home.js
   Sections:
   1. Shared utilities
   2. Nav scroll
   3. Animated counters
   4. Live activity feed
   5. Scroll reveal
   6. Pricing tab switcher
   7. Plan calculator
============================================================================= */


/* -----------------------------------------------------------------------------
   1. SHARED UTILITIES
----------------------------------------------------------------------------- */

/**
 * Format a number for display in counters.
 * Values ≥ 1 000 are shown as "Xk" (e.g. 50 000 → "50K").
 */
function fmtCount(n) {
  return n >= 1000 ? Math.round(n / 1000) + "K" : String(n);
}

/**
 * Format a rupee amount (rounds to nearest integer, adds ₹ and en-IN commas).
 */
function fmtRupee(n) {
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

/**
 * Tiny helper: add an IntersectionObserver that fires once per element.
 */
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
   2. NAV SCROLL — adds .is-scrolled to #siteNav once the user scrolls
----------------------------------------------------------------------------- */
(function () {
  var nav = document.getElementById("siteNav");
  if (!nav) return;

  window.addEventListener("scroll", function () {
    nav.classList.toggle("is-scrolled", window.scrollY > 8);
  }, { passive: true });
})();


/* -----------------------------------------------------------------------------
   3. ANIMATED COUNTERS — [data-target] on .proof-num elements
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
      var ease = 1 - Math.pow(1 - t, 3);          /* cubic ease-out */
      el.textContent = fmtCount(Math.round(ease * target));
      if (t < 1) requestAnimationFrame(step);
      else        el.textContent = fmtCount(target);
    }
    requestAnimationFrame(step);
  }

  onEnter(Array.from(nums), animateCounter, { threshold: 0.5 });
})();


/* -----------------------------------------------------------------------------
   4. LIVE ACTIVITY FEED — cycles fake events into #activityFeed every 3.2 s
----------------------------------------------------------------------------- */
(function () {
  var feed = document.getElementById("activityFeed");
  if (!feed) return;

  var events = [
    { tag: "checkin", label: "CHECK-IN", msg: "Sunita K. · Alpha Fitness"  },
    { tag: "payment", label: "PAYMENT",  msg: "₹4,500 · FitZone Elite"     },
    { tag: "enroll",  label: "ENROLL",   msg: "New member · 3-month plan"   },
    { tag: "checkin", label: "CHECK-IN", msg: "Mohit R. · GPS verified"     },
    { tag: "order",   label: "ORDER",    msg: "Creatine 500g · MuscleHub"   },
    { tag: "checkin", label: "CHECK-IN", msg: "Deepa V. · FaceID verified"  },
    { tag: "payment", label: "PAYMENT",  msg: "₹2,100 · CoreFit Studio"     },
  ];

  var ei = 0;

  setInterval(function () {
    var ev  = events[ei % events.length];
    ei++;

    var now = new Date();
    var hh  = String(now.getHours()).padStart(2, "0");
    var mm  = String(now.getMinutes()).padStart(2, "0");

    var row       = document.createElement("div");
    row.className = "feed-row feed-row--new";
    row.innerHTML =
      '<span class="feed-time">'         + hh + ":" + mm   + "</span>" +
      '<span class="feed-tag ' + ev.tag + '">' + ev.label  + "</span>" +
      '<span class="feed-msg">'           + ev.msg          + "</span>";

    feed.insertBefore(row, feed.firstChild);

    /* Remove the --new class on next frame (triggers CSS transition) */
    setTimeout(function () { row.classList.remove("feed-row--new"); }, 50);

    /* Cap at 8 visible rows; fade then remove the oldest */
    var rows = feed.querySelectorAll(".feed-row");
    if (rows.length > 8) {
      var last = rows[rows.length - 1];
      last.classList.add("faded");
      setTimeout(function () { last.remove(); }, 600);
    }
  }, 3200);
})();


/* -----------------------------------------------------------------------------
   5. SCROLL REVEAL — adds .visible to elements as they enter the viewport
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
  ].join(", ");

  var els = Array.from(document.querySelectorAll(SELECTORS));
  if (!els.length) return;

  els.forEach(function (el) { el.classList.add("reveal"); });

  onEnter(els, function (el) { el.classList.add("visible"); }, { threshold: 0.1 });
})();


/* -----------------------------------------------------------------------------
   6. PRICING TAB SWITCHER — .s5-tab[data-panel] toggles .s5-panel visibility
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


/* -----------------------------------------------------------------------------
   7. PLAN CALCULATOR
----------------------------------------------------------------------------- */
(function () {

  /* ── Pricing constants ─────────────────────────────────────────────────── */
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

  /* ── Helpers ───────────────────────────────────────────────────────────── */
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

    var html = costRow(
      months + "-month base (1 branch)",
      fmtRupee(plan.base)
    );

    if (extra > 0) {
      var addlLabel = (branches - 1) + " additional branch" + (branches - 1 > 1 ? "es" : "");
      html += costRow(addlLabel, fmtRupee(extra));
    }

    html += costRow("Effective monthly cost", fmtRupee(monthly) + "/mo");

    return { html: html, total: total, monthly: monthly };
  }

  /* ── DOM refs ──────────────────────────────────────────────────────────── */
  var branchSlider  = document.getElementById("calc-branches");
  var branchDisplay = document.getElementById("calc-branches-display");
  var branchesGrid  = document.getElementById("calc-branches-grid");
  var runBtn        = document.getElementById("calc-run-btn");

  if (!branchSlider || !runBtn) return;   /* bail if calculator isn't on page */

  /* ── Branch count slider → rebuild per-branch member inputs ───────────── */
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

  /* ── Result-panel plan tab switching ──────────────────────────────────── */
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

  /* ── Calculate button ─────────────────────────────────────────────────── */
  runBtn.addEventListener("click", function () {
    var gymName      = (document.getElementById("calc-gym-name").value.trim()) || "Your Gym";
    var branches     = parseInt(branchSlider.value, 10);
    var memberInputs = Array.from(branchesGrid.querySelectorAll(".s6-branch-input"));

    var branchMembers = memberInputs.map(function (inp) {
      return parseInt(inp.value, 10) || 0;
    });

    var totalMembers = branchMembers.reduce(function (a, b) { return a + b; }, 0);

    /* Require at least one member count */
    if (totalMembers === 0) {
      if (memberInputs[0]) memberInputs[0].focus();
      return;
    }

    /* ── PAYG breakdown ─── */
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

    /* ── Fixed plan breakdowns ─── */
    var f3  = calcFixed(3,  branches);
    var f6  = calcFixed(6,  branches);
    var f12 = calcFixed(12, branches);

    document.getElementById("fixed3-rows").innerHTML     = f3.html;
    document.getElementById("fixed3-total").textContent  = fmtRupee(f3.total);
    document.getElementById("fixed6-rows").innerHTML     = f6.html;
    document.getElementById("fixed6-total").textContent  = fmtRupee(f6.total);
    document.getElementById("fixed12-rows").innerHTML    = f12.html;
    document.getElementById("fixed12-total").textContent = fmtRupee(f12.total);

    /* ── Recommendation logic ─── */
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

    /* ── Savings comparison grid ─── */
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

    /* ── Reveal result panels ─── */
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