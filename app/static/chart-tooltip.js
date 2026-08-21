// Shared styled chart tooltip -- every chart in app/charts.py puts its hover
// text in a data-tooltip attribute instead of a native SVG <title>, so every
// chart (old and new), on every page (including the standalone client-report
// page, which doesn't load base.html's script), gets one on-brand tooltip
// component instead of the browser's unstyled native one. No build step, no
// library -- vanilla JS, delegated on document since charts are rendered
// server-side per page, not dynamically added.
(function () {
  var tooltipEl = null;

  function showChartTooltip(target) {
    if (!tooltipEl) {
      tooltipEl = document.createElement("div");
      tooltipEl.className = "chart-tooltip";
      document.body.appendChild(tooltipEl);
    }
    tooltipEl.textContent = target.getAttribute("data-tooltip");
    tooltipEl.style.display = "block";
    tooltipEl.style.left = "0px";
    tooltipEl.style.top = "0px";
    var r = target.getBoundingClientRect();
    var tw = tooltipEl.offsetWidth, th = tooltipEl.offsetHeight;
    var left = Math.max(4, Math.min(window.innerWidth - tw - 4, r.left + r.width / 2 - tw / 2));
    var top = Math.max(4, r.top - th - 8);
    tooltipEl.style.left = left + "px";
    tooltipEl.style.top = top + "px";
  }

  function hideChartTooltip() {
    if (tooltipEl) tooltipEl.style.display = "none";
  }

  document.addEventListener("mouseover", function (e) {
    var el = e.target.closest && e.target.closest("[data-tooltip]");
    if (el) showChartTooltip(el);
  });
  document.addEventListener("mouseout", function (e) {
    var el = e.target.closest && e.target.closest("[data-tooltip]");
    if (el) hideChartTooltip();
  });
})();
