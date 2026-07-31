/**
 * Stat card CountUp animation.
 * Vanilla JS only — no jQuery, no GSAP, no AOS.
 * Requires CountUp.js (loaded separately, see include snippet below).
 * Reads the *rendered* Django value from data-countup / textContent,
 * so no Django context or template logic is touched.
 */
document.addEventListener('DOMContentLoaded', function () {
  if (typeof countUp === 'undefined' || !countUp.CountUp) {
    // CountUp.js not loaded — leave server-rendered values as-is.
    return;
  }

  var valueEls = document.querySelectorAll('.db-stat-value[data-countup]');

  valueEls.forEach(function (el, index) {
    var raw = (el.getAttribute('data-countup') || el.textContent || '').trim();

    // Extract the first numeric run (handles "1,234", "₹1,234.50", "85%", etc.)
    var match = raw.match(/[\d,]+(\.\d+)?/);
    if (!match) return; // Non-numeric value (e.g. "N/A") — leave untouched

    var numericStr = match[0];
    var prefix = raw.slice(0, match.index);
    var suffix = raw.slice(match.index + numericStr.length);
    var endValue = parseFloat(numericStr.replace(/,/g, ''));
    if (isNaN(endValue)) return;

    var decimalPlaces = numericStr.includes('.')
      ? numericStr.split('.')[1].length
      : 0;

    // Keep prefix/suffix (currency symbols, %, etc.) wrapped around the animated number
    el.textContent = ''; // clear, CountUp will fill the number part
    var prefixNode = document.createTextNode(prefix);
    var numberSpan = document.createElement('span');
    var suffixNode = document.createTextNode(suffix);
    el.appendChild(prefixNode);
    el.appendChild(numberSpan);
    el.appendChild(suffixNode);

    var counter = new countUp.CountUp(numberSpan, endValue, {
      duration: 1.4,
      separator: ',',
      decimalPlaces: decimalPlaces,
      useEasing: true
    });

    // Stagger start to roughly follow the card's CSS entrance delay
    var delay = Math.min(index, 10) * 60;

    if (!counter.error) {
      setTimeout(function () {
        counter.start();
      }, delay);
    } else {
      // Fallback: restore original text if CountUp fails for any reason
      el.textContent = raw;
    }
  });
});