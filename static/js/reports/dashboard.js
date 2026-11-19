/*
 * The sales report (reports/dashboard.html): the hourly revenue line and
 * the payment-method ring (Chart.js, static/vendor), coloured for light or
 * dark, redrawn by a reload when the theme switches; and the Export menu,
 * whose links carry the report's filters (the period, the outlet) to the
 * export. The filters themselves are shared change actions
 * (core/page_actions.js).
 */
import { theme } from "../core/app.js";
import { delegate } from "../core/dom.js";
import { pageData } from "../core/page_data.js";
import { rupees } from "../core/money.js";
import { chartColours, exportUrl } from "./dashboard_parts.js";

function drawCharts(Chart, dark) {
  const c = chartColours(dark);
  Chart.defaults.color = c.text;
  Chart.defaults.font.family = "'Outfit', sans-serif";
  Chart.defaults.font.size = 11;
  const payments = pageData("payments-data");
  const hourly = pageData("hourly-data");

  const ring = document.getElementById("paymentChart");
  if (ring && Array.isArray(payments)) {
    new Chart(ring, {
      type: "doughnut",
      data: {
        labels: payments.map((p) => p.method),
        datasets: [{ data: payments.map((p) => p.total || 0), backgroundColor: c.payments, borderWidth: 1, borderColor: c.ring, hoverOffset: 4 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { padding: 20, usePointStyle: true, boxWidth: 8, font: { family: "'Outfit', sans-serif", size: 10, weight: "500" } } } },
        cutout: "80%",
      },
    });
  }

  const line = document.getElementById("salesChart");
  if (line && Array.isArray(hourly)) {
    const grad = line.getContext("2d").createLinearGradient(0, 0, 0, 350);
    grad.addColorStop(0, c.fill[0]);
    grad.addColorStop(1, c.fill[1]);
    new Chart(line, {
      type: "line",
      data: {
        labels: hourly.map((h) => h.label),
        datasets: [{
          label: "Revenue", data: hourly.map((h) => h.total || 0), borderColor: c.line, borderWidth: 2, backgroundColor: grad, fill: true,
          tension: 0.4, pointBackgroundColor: c.ring, pointBorderColor: c.line, pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: c.tip[0], titleColor: c.tip[1], bodyColor: c.tip[1],
            titleFont: { family: "'DM Serif Display', serif", size: 14 }, bodyFont: { family: "'Outfit', sans-serif", size: 13 },
            padding: 12, displayColors: false, cornerRadius: 0,
            callbacks: { label: (ctx) => rupees(ctx.parsed.y) },
          },
        },
        scales: {
          y: { beginAtZero: true, grid: { color: c.grid, drawBorder: false }, border: { display: false }, ticks: { padding: 10 } },
          x: { grid: { display: false }, border: { display: false }, ticks: { padding: 10 } },
        },
      },
    });
  }
}

if (typeof document !== "undefined") {
  delegate(document.body, {
    "report.export": (link, event) => {
      event.preventDefault();
      window.location.href = exportUrl(link.dataset.url, window.location.search, link.dataset.type);
    },
  });
  if (window.Chart) drawCharts(window.Chart, theme.isDark());
  window.addEventListener("themeChanged", () => window.location.reload());
}
