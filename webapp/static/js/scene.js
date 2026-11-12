/*
 * S2 장면 상세 — BEV(Canvas 2D) + 표·이미지·BEV 연결 강조 + 키보드 이동.
 *
 * 점군: GET /scenes/<id>/points.bin → float32 LE (x 앞, y 왼쪽, z 위, 반사 강도) × N
 * 박스: #scene-data 의 footprint = [[앞(m), 왼쪽(m)] × 4]
 * 화면: 위 = 앞, 오른쪽 = 오른쪽(= -왼쪽). 센서는 아래 가운데.
 * 색은 모두 CSS 토큰(--viridis-*, --cls-*, --viz-*)에서 읽는다.
 */
(function () {
  "use strict";
  var KSE = window.KSE || {};
  var dataEl = document.getElementById("scene-data");
  if (!dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var grid = document.querySelector("[data-scene]");
  var camera = document.getElementById("camera");
  var bevBox = document.getElementById("bev");
  var canvas = document.getElementById("bev-canvas");
  var ctx = canvas.getContext("2d");
  var colorSel = document.getElementById("bev-color");
  var selectionText = document.querySelector("[data-selection]");

  /* ------------------------------------------------ tokens */
  function cssVar(el, name) { return getComputedStyle(el).getPropertyValue(name).trim(); }
  function hexToRgb(h) {
    h = h.replace("#", "");
    if (h.length === 3) h = h.replace(/(.)/g, "$1$1");
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  var palette = {};
  var ramp = [];
  function readTokens() {
    palette = {
      bg: cssVar(bevBox, "--viz-bg"),
      grid: cssVar(bevBox, "--viz-grid"),
      gridStrong: cssVar(bevBox, "--viz-grid-strong"),
      text: cssVar(bevBox, "--viz-text-muted"),
      sensor: cssVar(bevBox, "--viz-sensor"),
      fontMono: cssVar(document.documentElement, "--font-mono"),
      cls: {},
      ink: {},
    };
    ["car", "van", "truck", "pedestrian", "person-sitting", "cyclist", "tram", "misc"].forEach(function (s) {
      palette.cls[s] = cssVar(bevBox, "--cls-" + s);
      palette.ink[s] = cssVar(bevBox, "--cls-" + s + "-ink");
    });
    var stops = [0, 1, 2, 3, 4].map(function (i) { return hexToRgb(cssVar(bevBox, "--viridis-" + i)); });
    ramp = [];
    for (var k = 0; k < 64; k++) {
      // start at 20 % of viridis so the lowest values stay visible on the dark well
      var t = (0.2 + 0.8 * (k / 63)) * 4, i0 = Math.min(3, Math.floor(t)), f = t - i0;
      var a = stops[i0], b = stops[i0 + 1];
      ramp.push("rgb(" + Math.round(a[0] + (b[0] - a[0]) * f) + "," + Math.round(a[1] + (b[1] - a[1]) * f) + "," + Math.round(a[2] + (b[2] - a[2]) * f) + ")");
    }
  }

  /* ------------------------------------------------ view state */
  var roi = data.roi;
  var view = { s: 1, ox: 0, oy: 0 };
  var W = 0, H = 0, dpr = 1;
  var pts = null;        // Float32Array
  var buckets = null;    // Array<Int32Array> per colour bucket
  var hl = null, selected = null;

  function fit() {
    var spanF = roi.fwd_max - roi.fwd_min, spanL = roi.left_max - roi.left_min;
    view.s = Math.min(W / (spanL * 1.04), H / (spanF * 1.06));
    view.ox = W / 2;
    view.oy = H - (H - spanF * view.s) / 2 + roi.fwd_min * view.s;
  }
  function toScreen(f, l) { return [view.ox - l * view.s, view.oy - f * view.s]; }
  function toWorld(x, y) { return [(view.oy - y) / view.s, (view.ox - x) / view.s]; }

  function resize() {
    var r = bevBox.getBoundingClientRect();
    dpr = window.devicePixelRatio || 1;
    var first = W === 0;
    var oldW = W, oldH = H;
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    if (first) fit();
    else { view.ox += (W - oldW) / 2; view.oy += (H - oldH); }
    draw();
  }

  function bucketize() {
    if (!pts) return;
    var mode = colorSel ? colorSel.value : "z";
    var n = pts.length / 4, lists = [];
    for (var b = 0; b < 64; b++) lists.push([]);
    var lo = mode === "z" ? -2.0 : 0.0, hi = mode === "z" ? 2.5 : 1.0;
    for (var i = 0; i < n; i++) {
      var v = mode === "z" ? pts[i * 4 + 2] : pts[i * 4 + 3];
      var k = Math.round(((v - lo) / (hi - lo)) * 63);
      lists[k < 0 ? 0 : k > 63 ? 63 : k].push(i);
    }
    buckets = lists.map(function (l) { return Int32Array.from(l); });
  }

  /* ------------------------------------------------ drawing */
  var raf = 0;
  var firstDrawn = false;
  function draw() {
    if (raf) return;
    raf = requestAnimationFrame(function () { raf = 0; paint(); });
  }
  function paint() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = palette.bg;
    ctx.fillRect(0, 0, W, H);

    // range rings every 10 m + labels
    var o = toScreen(0, 0);
    ctx.lineWidth = 1;
    ctx.font = "11px " + palette.fontMono;
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    for (var r = 10; r <= 80; r += 10) {
      ctx.strokeStyle = r % 20 === 0 ? palette.gridStrong : palette.grid;
      ctx.beginPath();
      ctx.arc(o[0], o[1], r * view.s, Math.PI, 2 * Math.PI);
      ctx.stroke();
      var lp = toScreen(r, 0);
      ctx.fillStyle = palette.text;
      ctx.fillText(r + " m", lp[0] + 4, lp[1] - 2);
    }
    // centre line
    ctx.strokeStyle = palette.grid;
    ctx.setLineDash([4, 6]);
    ctx.beginPath();
    var a = toScreen(roi.fwd_max, 0);
    ctx.moveTo(o[0], o[1]); ctx.lineTo(a[0], a[1]);
    ctx.stroke();
    ctx.setLineDash([]);

    // points (bucketed by colour → few fillStyle changes)
    if (pts && buckets) {
      var size = Math.max(1.2, Math.min(3, view.s * 0.12));
      var half = size / 2;
      for (var b = 0; b < 64; b++) {
        var idx = buckets[b];
        if (!idx.length) continue;
        ctx.fillStyle = ramp[b];
        for (var j = 0; j < idx.length; j++) {
          var p = idx[j] * 4;
          var x = view.ox - pts[p + 1] * view.s, y = view.oy - pts[p] * view.s;
          if (x < -2 || y < -2 || x > W + 2 || y > H + 2) continue;
          ctx.fillRect(x - half, y - half, size, size);
        }
      }
      // U-11 측정용: 점군을 처음 다 그린 시점 (performance.getEntriesByName("kse:bev-first-draw"))
      if (!firstDrawn) {
        firstDrawn = true;
        try { performance.mark("kse:bev-first-draw"); } catch (e) { /* 측정 불가 브라우저 */ }
      }
    }

    // boxes (footprints)
    var linking = hl !== null || selected !== null;
    data.footprints.forEach(function (fp, i) {
      var id = data.footprint_ids[i], slug = data.classes[i];
      var active = id === hl || id === selected;
      ctx.globalAlpha = linking && !active ? 0.35 : 1;
      ctx.beginPath();
      fp.forEach(function (c, k) {
        var s = toScreen(c[0], c[1]);
        if (k === 0) ctx.moveTo(s[0], s[1]); else ctx.lineTo(s[0], s[1]);
      });
      ctx.closePath();
      ctx.strokeStyle = palette.cls[slug] || palette.cls.misc;
      ctx.lineWidth = active ? 3 : 1.75;
      ctx.stroke();
      // number tag
      var cx = 0, cy = 0;
      fp.forEach(function (c) { var s = toScreen(c[0], c[1]); cx += s[0] / 4; cy += s[1] / 4; });
      var label = String(id);
      ctx.font = "600 11px " + palette.fontMono;
      var tw = ctx.measureText(label).width + 8;
      var minX = Infinity, minY = Infinity;
      fp.forEach(function (c) { var s = toScreen(c[0], c[1]); minX = Math.min(minX, s[0]); minY = Math.min(minY, s[1]); });
      var tx = Math.max(minX, cx - tw / 2), ty = minY - 16;
      ctx.fillStyle = palette.cls[slug] || palette.cls.misc;
      ctx.fillRect(tx, ty, tw, 15);
      ctx.fillStyle = palette.ink[slug] || palette.ink.misc;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, tx + tw / 2, ty + 8);
      ctx.globalAlpha = 1;
    });

    // sensor + heading
    ctx.fillStyle = palette.sensor;
    ctx.beginPath();
    ctx.moveTo(o[0], o[1] - 9);
    ctx.lineTo(o[0] - 6, o[1] + 4);
    ctx.lineTo(o[0] + 6, o[1] + 4);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = palette.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = "11px " + palette.fontMono;
    if (o[1] + 8 < H - 4) ctx.fillText("센서", o[0], o[1] + 7);
  }

  /* ------------------------------------------------ load points */
  function loadPoints() {
    var loading = bevBox.querySelector("[data-bev-loading]");
    var error = bevBox.querySelector("[data-bev-error]");
    // progress bar only if loading takes longer than 300 ms
    loading.style.visibility = "hidden";
    var slow = setTimeout(function () { loading.style.visibility = "visible"; }, 300);
    fetch(bevBox.getAttribute("data-src"))
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        pts = new Float32Array(buf);
        bucketize();
        loading.hidden = true;
        draw();
      })
      .catch(function () { loading.hidden = true; error.hidden = false; })
      .then(function () { clearTimeout(slow); });
  }

  /* ------------------------------------------------ interaction: zoom / pan */
  function zoomAt(factor, x, y) {
    var w = toWorld(x, y);
    view.s = Math.max(1, Math.min(200, view.s * factor));
    view.ox = x + w[1] * view.s;
    view.oy = y + w[0] * view.s;
    draw();
  }
  bevBox.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = canvas.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });

  var drag = null;
  bevBox.addEventListener("pointerdown", function (e) {
    drag = { x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy, moved: false };
    bevBox.setPointerCapture(e.pointerId);
  });
  bevBox.addEventListener("pointermove", function (e) {
    if (drag) {
      var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.ox = drag.ox + dx; view.oy = drag.oy + dy;
      draw();
      return;
    }
    var r = canvas.getBoundingClientRect();
    setHighlight(hitTest(e.clientX - r.left, e.clientY - r.top));
  });
  bevBox.addEventListener("pointerup", function (e) {
    var wasDrag = drag && drag.moved;
    drag = null;
    if (!wasDrag) {
      var r = canvas.getBoundingClientRect();
      var id = hitTest(e.clientX - r.left, e.clientY - r.top);
      select(id === selected ? null : id);
    }
  });
  bevBox.addEventListener("pointerleave", function () { if (!drag) setHighlight(null); });

  document.querySelectorAll("[data-bev-zoom]").forEach(function (b) {
    b.addEventListener("click", function () { zoomAt(b.getAttribute("data-bev-zoom") === "in" ? 1.3 : 1 / 1.3, W / 2, H / 2); });
  });
  var resetBtn = document.querySelector("[data-bev-reset]");
  if (resetBtn) resetBtn.addEventListener("click", function () { fit(); draw(); });
  bevBox.addEventListener("keydown", function (e) {
    var step = 40;
    if (e.key === "+" || e.key === "=") zoomAt(1.2, W / 2, H / 2);
    else if (e.key === "-" || e.key === "_") zoomAt(1 / 1.2, W / 2, H / 2);
    else if (e.key === "0") { fit(); draw(); }
    else if (e.key === "ArrowLeft") view.ox += step;
    else if (e.key === "ArrowRight") view.ox -= step;
    else if (e.key === "ArrowUp") view.oy += step;
    else if (e.key === "ArrowDown") view.oy -= step;
    else return;
    e.preventDefault();
    e.stopPropagation(); // arrows pan here instead of changing scene
    draw();
  });
  if (colorSel) colorSel.addEventListener("change", function () { bucketize(); draw(); });

  function hitTest(x, y) {
    var w = toWorld(x, y), f = w[0], l = w[1];
    for (var i = data.footprints.length - 1; i >= 0; i--) {
      var fp = data.footprints[i], inside = false;
      for (var a = 0, b = 3; a < 4; b = a++) {
        var fa = fp[a][0], la = fp[a][1], fb = fp[b][0], lb = fp[b][1];
        if ((la > l) !== (lb > l) && f < ((fb - fa) * (l - la)) / (lb - la) + fa) inside = !inside;
      }
      if (inside) return data.footprint_ids[i];
    }
    return null;
  }

  /* ------------------------------------------------ linked highlight */
  function apply() {
    var active = hl !== null ? hl : selected;
    grid.classList.toggle("is-linking", active !== null);
    document.querySelectorAll("[data-obj]").forEach(function (el) {
      var id = parseInt(el.getAttribute("data-obj"), 10);
      el.classList.toggle("is-hl", id === hl || id === selected);
      if (el.tagName === "TR") {
        el.classList.toggle("is-selected", id === selected);
        el.setAttribute("aria-selected", id === selected ? "true" : "false");
      }
    });
    draw();
  }
  function setHighlight(id) { if (id !== hl) { hl = id; apply(); } }
  function select(id) {
    selected = id;
    if (selectionText) {
      if (id === null) selectionText.textContent = "객체를 고르면 이미지와 BEV에서 함께 강조됩니다.";
      else {
        var row = document.querySelector("tr[data-obj='" + id + "']");
        var label = row ? row.cells[1].textContent.trim() : "";
        selectionText.innerHTML = "";
        var strong = document.createElement("strong");
        strong.textContent = "선택한 객체: " + id + " " + label;
        selectionText.appendChild(strong);
      }
    }
    apply();
  }

  document.querySelectorAll("tr[data-obj], .ibox[data-obj]").forEach(function (el) {
    var id = parseInt(el.getAttribute("data-obj"), 10);
    if (el.classList.contains("ibox--dontcare")) return;
    el.addEventListener("mouseenter", function () { setHighlight(id); });
    el.addEventListener("mouseleave", function () { setHighlight(null); });
    el.addEventListener("click", function () { select(selected === id ? null : id); });
    if (el.tagName === "TR") {
      el.addEventListener("focus", function () { setHighlight(id); });
      el.addEventListener("blur", function () { setHighlight(null); });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(selected === id ? null : id); }
      });
    }
  });

  /* ------------------------------------------------ camera layers */
  document.querySelectorAll("[data-layer]").forEach(function (cb) {
    cb.addEventListener("change", function () {
      camera.setAttribute("data-" + cb.getAttribute("data-layer"), cb.checked ? "on" : "off");
    });
  });

  /* ------------------------------------------------ keyboard: ← → Esc */
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    if (KSE.isTypingTarget && KSE.isTypingTarget(e.target)) return;
    if (document.querySelector("dialog[open]")) return;
    var link = null;
    if (e.key === "ArrowLeft") link = document.querySelector("[data-nav-prev]");
    else if (e.key === "ArrowRight") link = document.querySelector("[data-nav-next]");
    else if (e.key === "Escape") {
      if (selected !== null) { select(null); return; }
      link = document.querySelector("[data-nav-back]");
    }
    if (link) { e.preventDefault(); location.href = link.href; }
  });

  /* ------------------------------------------------ boot */
  readTokens();
  document.addEventListener("kse:themechange", function () { readTokens(); bucketize(); draw(); });
  if (window.ResizeObserver) new ResizeObserver(resize).observe(bevBox);
  else window.addEventListener("resize", resize);
  resize();
  loadPoints();
})();
