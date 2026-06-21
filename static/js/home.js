document.addEventListener("DOMContentLoaded", () => {
  /* =========================
     RESET: no card open on load
  ========================= */
  document.querySelectorAll(".feature-card, .pricing-card").forEach((c) => {
    c.classList.remove("active");
  });

  /* =========================
     CARD CLICK HANDLERS
  ========================= */
  document.querySelectorAll(".feature-card").forEach((card) => {
    card.addEventListener("click", function () {
      toggleFeature(this);
    });
  });

  document.querySelectorAll(".pricing-card").forEach((card) => {
    card.addEventListener("click", function () {
      togglePricing(this);
    });
  });

  /* =========================
     MOBILE MENU
  ========================= */
  const menuBtn = document.getElementById("menuBtn");
  const mobileMenu = document.getElementById("mobileMenu");
  const closeMenu = document.getElementById("closeMenu");

  if (menuBtn && mobileMenu && closeMenu) {
    mobileMenu.classList.remove("active");

    const closeMobileMenu = () => {
      mobileMenu.classList.remove("active");
      menuBtn.setAttribute("aria-expanded", "false");
      document.body.style.overflow = "";
    };

    menuBtn.addEventListener("click", () => {
      mobileMenu.classList.add("active");
      menuBtn.setAttribute("aria-expanded", "true");
      document.body.style.overflow = "hidden";
    });

    closeMenu.addEventListener("click", closeMobileMenu);

    mobileMenu.addEventListener("click", (e) => {
      if (e.target === mobileMenu) closeMobileMenu();
    });

    mobileMenu.querySelectorAll("a, button").forEach((item) => {
      item.addEventListener("click", closeMobileMenu);
    });
  }

  /* =========================
     LAYOUT OFFSET ENGINE
     Stacks: notifBar → topbar → navbar
     and sets hero padding-top dynamically
  ========================= */
  function updateLayout() {
    const notifBar = document.getElementById("notifBar");
    const topbar = document.getElementById("topbar");
    const navbar = document.getElementById("navbar");
    const hero = document.getElementById("heroContent");

    const notifH =
      notifBar && !notifBar.classList.contains("hidden")
        ? notifBar.offsetHeight
        : 0;
    const topbarH = topbar ? topbar.offsetHeight : 0;
    const navbarH = navbar ? navbar.offsetHeight : 0;

    if (topbar) topbar.style.top = notifH + "px";
    if (navbar) navbar.style.top = notifH + topbarH + "px";
    if (hero) hero.style.paddingTop = notifH + topbarH + navbarH + 32 + "px";
  }

  updateLayout();
  window.addEventListener("resize", updateLayout);

  /* =========================
     NOTIFICATION CLOSE
  ========================= */
  const notifBar = document.getElementById("notifBar");
  const notifClose = document.getElementById("notifClose");

  if (notifBar && notifClose) {
    notifClose.addEventListener("click", () => {
      notifBar.classList.add("hidden");
      setTimeout(updateLayout, 420); // after CSS transition completes
    });
  }

  /* =========================
     FLASH MESSAGES
  ========================= */
  const messages = document.querySelectorAll(".flash-message");

  messages.forEach((msg, i) => {
    msg.style.opacity = "0";
    msg.style.transform = "translateY(-10px)";

    setTimeout(() => {
      msg.style.transition = "all 0.4s ease";
      msg.style.opacity = "1";
      msg.style.transform = "translateY(0)";
    }, i * 150);

    setTimeout(() => removeMessage(msg), 4000 + i * 200);

    const closeBtn = msg.querySelector(".close-btn");
    if (closeBtn) closeBtn.addEventListener("click", () => removeMessage(msg));
  });

  function removeMessage(msg) {
    if (!msg || !msg.isConnected) return;
    msg.style.transition = "all 0.3s ease";
    msg.style.opacity = "0";
    msg.style.transform = "translateY(-10px)";
    setTimeout(() => {
      if (msg.isConnected) msg.remove();
    }, 300);
  }

  /* =========================
     HERO COUNTERS
  ========================= */
  function animateCount(el, target) {
    if (!el) return;
    let current = 0;
    const duration = 1200;
    const stepTime = 16;
    const increment = Math.ceil(target / (duration / stepTime));
    const timer = setInterval(() => {
      current += increment;
      if (current >= target) {
        current = target;
        clearInterval(timer);
      }
      el.textContent = current;
    }, stepTime);
  }

  animateCount(document.getElementById("statExercise"), 20);
  animateCount(document.getElementById("statSatisfaction"), 92);

  /* =========================
     STATS API
  ========================= */
  window.addEventListener("load", () => {
    setTimeout(() => {
      fetch("/api/stats/")
        .then((res) => {
          if (!res.ok) throw new Error("API failed");
          return res.json();
        })
        .then((data) => {
          const users = data.total_users || 10;
          const display = users < 50 ? users : Math.ceil(users / 10) * 10 * 2;
          animateCount(document.getElementById("statUsers"), display);
        })
        .catch(() => animateCount(document.getElementById("statUsers"), 10));
    }, 2000);
  });

  /* =========================
     HERO ANIMATION
  ========================= */
  const animatedEls = document.querySelectorAll(".hero-animate");

  if (
    "IntersectionObserver" in window &&
    animatedEls.length &&
    window.innerWidth > 768
  ) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.style.animationPlayState = "running";
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 },
    );
    animatedEls.forEach((el) => {
      el.style.animationPlayState = "paused";
      observer.observe(el);
    });
  } else {
    animatedEls.forEach((el) => {
      el.style.opacity = "1";
      el.style.transform = "none";
    });
  }

  /* =========================
     SCROLL — NAVBAR BORDER
  ========================= */
  const navbarEl = document.getElementById("navbar");
  let ticking = false;

  function updateNavbar() {
    if (!navbarEl) return;
    navbarEl.style.borderBottom =
      window.scrollY > 10 ? "1px solid rgba(249,115,22,0.25)" : "";
    ticking = false;
  }

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        requestAnimationFrame(updateNavbar);
        ticking = true;
      }
    },
    { passive: true },
  );

  /* =========================
     SMOOTH SCROLL
  ========================= */
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (e) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
});
window.addEventListener('scroll', () => {
  const navbar = document.querySelector('.navbar');
  if (window.scrollY > 50) {
    navbar.classList.add('scrolled');
  } else {
    navbar.classList.remove('scrolled');
  }
});

/* =========================
   FEATURE CARD TOGGLE
========================= */
function toggleFeature(card) {
  const isActive = card.classList.contains("active");
  document
    .querySelectorAll(".feature-card")
    .forEach((c) => c.classList.remove("active"));
  if (!isActive) {
    card.classList.add("active");
    setTimeout(() => {
      const rect = card.getBoundingClientRect();
      if (rect.bottom > window.innerHeight - 20)
        window.scrollBy({
          top: rect.bottom - window.innerHeight + 40,
          behavior: "smooth",
        });
    }, 400);
  }
}

/* =========================
   PRICING CARD TOGGLE
========================= */
function togglePricing(card) {
  const isActive = card.classList.contains("active");
  document
    .querySelectorAll(".pricing-card")
    .forEach((c) => c.classList.remove("active"));
  if (!isActive) {
    card.classList.add("active");
    setTimeout(() => {
      const rect = card.getBoundingClientRect();
      if (rect.bottom > window.innerHeight - 20)
        window.scrollBy({
          top: rect.bottom - window.innerHeight + 40,
          behavior: "smooth",
        });
    }, 400);
  }
}
