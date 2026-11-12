/*
 * S5 처리 결과 — Chart.js 그래프 (9.7).
 * - 범주 = 클래스 색, 성능 = 중앙값 점 + 최소–최대 오차 막대 + 이상적 선형 증가선(점선).
 * - 색·글꼴은 CSS 토큰에서 읽고, 테마가 바뀌면 다시 그린다.
 * - 값은 결과 파일(f7_class_dist.csv, bench/summary.csv)에 있는 그대로 그린다. 계산하지 않는다.
 */
(function () {
  "use strict";
  var el = document.getElementById("pipeline-data");
  if (!el || !window.Chart) return;
  var data = JSON.parse(el.textContent);
  var root = document.documentElement;
  var charts = {};

  function v(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { position: "bottom", labels: { color: v("--text"), boxWidth: 12, boxHeight: 12, font: { family: v("--font-sans"), size: 12 } } },
        tooltip: {
          backgroundColor: v("--text"), titleColor: v("--bg"), bodyColor: v("--bg"),
          titleFont: { family: v("--font-sans"), weight: "600" }, bodyFont: { family: v("--font-mono"), size: 12 },
          padding: 8, cornerRadius: 6, displayColors: true,
        },
      },
      scales: {},
    };
  }
  function axis(title, extra) {
    var a = {
      title: { display: true, text: title, color: v("--text-muted"), font: { family: v("--font-sans"), size: 12, weight: "600" } },
      ticks: { color: v("--text-muted"), font: { family: v("--font-mono"), size: 12 } },
      grid: { color: v("--border") },
      border: { color: v("--border-strong") },
    };
    for (var k in extra || {}) a[k] = extra[k];
    return a;
  }

  /* ------------------------------------------------ 분포: 클래스 × 거리 */
  function drawDist() {
    var d = data.dist;
    var opts = baseOptions();
    opts.scales = { x: axis("거리 구간"), y: axis("객체 수 (개)", { beginAtZero: true }) };
    opts.plugins.tooltip.callbacks = { label: function (c) { return c.dataset.label + ": " + c.parsed.y.toLocaleString("ko-KR") + "개"; } };
    return new Chart(document.getElementById("chart-dist"), {
      type: "bar",
      data: {
        labels: d.bins,
        datasets: Object.keys(d.series).map(function (cls) {
          var c = v("--cls-" + data.slugs[cls]);
          return { label: data.labels[cls], data: d.series[cls], backgroundColor: c, borderColor: c, borderRadius: 2, maxBarThickness: 28 };
        }),
      },
      options: opts,
    });
  }

  /* ------------------------------------------------ 성능: 속도 향상 + 오차 막대 */
  var errorBars = {
    id: "kseErrorBars",
    afterDatasetsDraw: function (chart) {
      var ctx = chart.ctx;
      chart.data.datasets.forEach(function (ds, di) {
        if (!ds.errorBars) return;
        var meta = chart.getDatasetMeta(di);
        var y = chart.scales.y;
        ctx.save();
        ctx.strokeStyle = ds.borderColor;
        ctx.lineWidth = 1.5;
        meta.data.forEach(function (pt, i) {
          var e = ds.errorBars[i];
          var top = y.getPixelForValue(e[1]), bot = y.getPixelForValue(e[0]);
          ctx.beginPath();
          ctx.moveTo(pt.x, top); ctx.lineTo(pt.x, bot);
          ctx.moveTo(pt.x - 5, top); ctx.lineTo(pt.x + 5, top);
          ctx.moveTo(pt.x - 5, bot); ctx.lineTo(pt.x + 5, bot);
          ctx.stroke();
        });
        ctx.restore();
      });
    },
  };

  function drawSpeedup() {
    // bench/summary.csv의 speedup_vs_seq_median·min·max (가장 큰 규모)
    var pts = data.speedup.map(function (s) { return { x: s.cores, y: s.median, lo: s.lo, hi: s.hi }; });
    var opts = baseOptions();
    opts.scales = {
      x: axis("Spark local[N] 코어 수", { type: "linear", min: 0, max: 9, ticks: { stepSize: 1, autoSkip: false, maxRotation: 0, color: v("--text-muted"), font: { family: v("--font-mono"), size: 12 }, callback: function (val) { return [1, 4, 8].indexOf(val) >= 0 ? "local[" + val + "]" : ""; } } }),
      y: axis("순차 대비 속도 향상 (배)", { beginAtZero: true, suggestedMax: 9 }),
    };
    opts.plugins.tooltip.callbacks = {
      label: function (c) {
        if (c.dataset.errorBars) {
          var e = c.dataset.errorBars[c.dataIndex];
          return "중앙값 " + c.parsed.y.toFixed(2) + "배 (범위 " + e[0].toFixed(2) + "–" + e[1].toFixed(2) + ")";
        }
        return "이상적 선형 " + c.parsed.y + "배";
      },
    };
    return new Chart(document.getElementById("chart-speedup"), {
      type: "scatter",
      data: {
        datasets: [
          {
            label: "이상적 선형 증가", type: "line", data: [{ x: 1, y: 1 }, { x: 8, y: 8 }],
            borderColor: v("--text-muted"), borderDash: [6, 6], borderWidth: 1.5, pointRadius: 0, fill: false,
          },
          {
            label: "측정 (3회 중앙값, 최소–최대)", data: pts.map(function (p) { return { x: p.x, y: p.y }; }),
            errorBars: pts.map(function (p) { return [p.lo, p.hi]; }),
            backgroundColor: v("--accent"), borderColor: v("--accent"), pointRadius: 5, pointHoverRadius: 7,
          },
        ],
      },
      options: opts,
      plugins: [errorBars],
    });
  }

  var builders = { "chart-dist": drawDist, "chart-speedup": drawSpeedup };

  function ensure(id) {
    var canvas = document.getElementById(id);
    if (!canvas || charts[id]) return;
    var panel = canvas.closest("[role=tabpanel]");
    if (panel && panel.hidden) return; // build when its tab is first shown
    charts[id] = builders[id]();
  }
  function ensureAll() { Object.keys(builders).forEach(ensure); }
  function rebuild() {
    Object.keys(charts).forEach(function (id) { charts[id].destroy(); delete charts[id]; });
    ensureAll();
  }

  document.addEventListener("kse:tabchange", function () { requestAnimationFrame(ensureAll); });
  document.addEventListener("kse:themechange", function () { requestAnimationFrame(rebuild); });
  ensureAll();
})();
