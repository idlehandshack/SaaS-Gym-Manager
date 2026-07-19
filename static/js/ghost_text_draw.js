(function () {
  const textEl = document.getElementById('ghostText');
  const wrap = document.querySelector('.brand-heading-wrap');
  if (!textEl || !wrap) return;

  const raw = textEl.textContent.trim();

  function buildLetters() {
    textEl.textContent = '';
    [...raw].forEach((char) => {
      const tspan = document.createElementNS('http://www.w3.org/2000/svg', 'tspan');
      tspan.textContent = char === ' ' ? '\u00A0' : char;
      tspan.classList.add('ghost-letter');
      textEl.appendChild(tspan);
    });
  }

  function fitToWidth() {
    // pehle CSS wale (max) font-size pe reset karo, taaki chote naam
    // hamesha apni full size use karein
    textEl.style.fontSize = '';
    const available = wrap.clientWidth * 0.94; // thoda safety margin
    const actual = textEl.getBBox().width;

    if (actual > available && actual > 0) {
      const currentSize = parseFloat(getComputedStyle(textEl).fontSize);
      const scaled = currentSize * (available / actual);
      textEl.style.fontSize = scaled + 'px';
    }
  }

  buildLetters();
  requestAnimationFrame(fitToWidth);

  // window resize pe bhi refit karo (desktop↔tablet↔mobile switch)
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => requestAnimationFrame(fitToWidth), 150);
  });
})();