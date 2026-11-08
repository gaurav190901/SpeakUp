// SpeakUp — small shared behaviours (show-password toggles, dashboard charts).
(function () {
  "use strict";

  // "Show password" checkboxes: data-show-password="id1 id2"
  document.querySelectorAll("[data-show-password]").forEach((box) => {
    box.addEventListener("change", () => {
      box.dataset.showPassword.split(/\s+/).forEach((id) => {
        const input = document.getElementById(id);
        if (input) input.type = box.checked ? "text" : "password";
      });
    });
  });

  // Simple dependency-free line charts: <svg class="sparkline" data-series="[...]">
  const NS = "http://www.w3.org/2000/svg";
  function make(tag, attrs) {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  }

  document.querySelectorAll("svg.sparkline").forEach((svg) => {
    let values;
    try { values = JSON.parse(svg.dataset.series || "[]"); } catch (e) { values = []; }
    if (!values.length) return;
    const colour = svg.dataset.colour || "#6C63FF";
    const W = 300, H = 110, P = 8;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("preserveAspectRatio", "none");
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const step = values.length > 1 ? (W - P * 2) / (values.length - 1) : 0;
    const pts = values.map((v, i) => [
      values.length > 1 ? P + i * step : W / 2,
      H - P - ((v - min) / span) * (H - P * 2),
    ]);
    const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = line + ` L ${pts[pts.length - 1][0].toFixed(1)} ${H - P} L ${pts[0][0].toFixed(1)} ${H - P} Z`;
    svg.appendChild(make("path", { d: area, fill: colour, "fill-opacity": "0.12" }));
    svg.appendChild(make("path", { d: line, fill: "none", stroke: colour, "stroke-width": "2.5",
      "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
    const last = pts[pts.length - 1];
    svg.appendChild(make("circle", { cx: last[0], cy: last[1], r: 4, fill: colour }));
    const label = document.createElement("span");
    label.className = "chart-last";
    const latest = values[values.length - 1];
    label.textContent = "Latest: " + (Number.isInteger(latest) ? latest : latest.toFixed(1));
    svg.parentNode.appendChild(label);
    if (values.length < 2) {
      const hint = document.createElement("span");
      hint.className = "chart-hint";
      hint.textContent = "Practise again to see your trend";
      svg.parentNode.appendChild(hint);
    }
  });
})();
