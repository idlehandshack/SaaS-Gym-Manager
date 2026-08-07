(function () {
  const ENDPOINT = "/api/public/live-stats/";
  const REFRESH_MS = 3600000;

  const els = {
    members: document.getElementById("statMembers"),
    gyms: document.getElementById("statGyms"),
    revenue: document.getElementById("statRevenue"),
    uptime: document.getElementById("statUptime"),
  };

  if (!els.members) return;

  let current = { members: 0, gyms: 0, revenue: 0, uptime: 0 };

  function formatINR(amount) {
    amount = Number(amount) || 0;
    if (amount >= 10000000) return "₹" + (amount / 10000000).toFixed(2) + "Cr";
    if (amount >= 100000) return "₹" + (amount / 100000).toFixed(2) + "L";
    if (amount >= 1000) return "₹" + (amount / 1000).toFixed(1) + "K";
    return "₹" + amount.toFixed(0);
  }

  function animateNumber(el, from, to, formatter, duration = 900) {
    from = Number(from) || 0;
    to = Number(to) || 0;
    const start = performance.now();
    function step(now) {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = from + (to - from) * eased;
      el.textContent = formatter(value);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = formatter(to);
    }
    requestAnimationFrame(step);
  }

  function applyStats(data) {
    animateNumber(els.members, current.members, data.members, (v) =>
      Math.round(v).toLocaleString("en-IN")
    );
    animateNumber(els.gyms, current.gyms, data.gyms, (v) => Math.round(v).toString());
    animateNumber(els.revenue, current.revenue, data.revenue, formatINR);
    els.uptime.textContent = Number(data.uptime).toFixed(2) + "%";

    current = data;
  }

  async function refresh() {
    try {
      const res = await fetch(ENDPOINT, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error("bad status " + res.status);
      const data = await res.json();
      applyStats(data);
    } catch (err) {
      console.warn("live-stats refresh failed, retrying next cycle", err);
    }
  }

  refresh();
  setInterval(refresh, REFRESH_MS);
})();