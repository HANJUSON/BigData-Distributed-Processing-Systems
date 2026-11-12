/*
 * KITTI Scene Explorer — shared behaviour (no build step, no framework).
 *
 * Rule (DB NR-1): this file never filters or aggregates data rows. It only
 * changes how already-received rows are displayed (sort order, visibility).
 */
(function () {
  "use strict";

  var KSE = (window.KSE = window.KSE || {});
  var THEME_KEY = "kse-theme";
  var root = document.documentElement;

  /* ---------------------------------------------------------------- */
  /* Storage — always guarded (private mode / blocked storage)          */
  /* ---------------------------------------------------------------- */
  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* storage unavailable: theme simply isn't remembered */ }
    return null;
  }

  function isTypingTarget(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }
  KSE.isTypingTarget = isTypingTarget;

  /* ---------------------------------------------------------------- */
  /* Theme toggle (system default, explicit choice persisted)           */
  /* ---------------------------------------------------------------- */
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function effectiveTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") return explicit;
    return media && media.matches ? "dark" : "light";
  }
  KSE.effectiveTheme = effectiveTheme;

  function syncThemeButtons() {
    var dark = effectiveTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var label = dark ? btn.getAttribute("data-label-light") : btn.getAttribute("data-label-dark");
      btn.setAttribute("aria-label", label);
      btn.setAttribute("title", label);
      btn.setAttribute("aria-pressed", dark ? "true" : "false");
    });
  }

  function setTheme(theme) {
    root.setAttribute("data-theme", theme);
    store(THEME_KEY, theme);
    syncThemeButtons();
    document.dispatchEvent(new CustomEvent("kse:themechange", { detail: { theme: theme } }));
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-toggle]");
    if (btn) setTheme(effectiveTheme() === "dark" ? "light" : "dark");
  });
  if (media && media.addEventListener) {
    media.addEventListener("change", function () {
      syncThemeButtons();
      document.dispatchEvent(new CustomEvent("kse:themechange", { detail: { theme: effectiveTheme() } }));
    });
  }

  /* ---------------------------------------------------------------- */
  /* Mobile nav                                                        */
  /* ---------------------------------------------------------------- */
  function initNav() {
    var toggle = document.querySelector("[data-nav-toggle]");
    var nav = document.getElementById("primary-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(open));
      nav.classList.toggle("is-open", open);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("is-open")) {
        nav.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  }

  /* ---------------------------------------------------------------- */
  /* Toasts — bottom-right, 4 s, optional action (e.g. 되돌리기)        */
  /* ---------------------------------------------------------------- */
  KSE.toast = function (message, opts) {
    opts = opts || {};
    var region = document.getElementById("toast-region");
    if (!region) return;
    var el = document.createElement("div");
    el.className = "toast toast--" + (opts.tone || "ok");
    var icon = document.createElement("span");
    icon.className = "toast__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = opts.tone === "danger" ? "✕" : opts.tone === "info" ? "ⓘ" : "✓";
    var msg = document.createElement("span");
    msg.className = "toast__msg";
    msg.textContent = message;
    el.appendChild(icon);
    el.appendChild(msg);
    if (opts.action) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "toast__action";
      b.textContent = opts.action.label;
      b.addEventListener("click", function () { opts.action.onClick(); dismiss(); });
      el.appendChild(b);
    }
    region.appendChild(el);
    var timer = setTimeout(dismiss, opts.duration || 4000);
    function dismiss() {
      clearTimeout(timer);
      el.classList.add("is-leaving");
      setTimeout(function () { el.remove(); }, 220);
    }
    return dismiss;
  };

  /* ---------------------------------------------------------------- */
  /* Shortcut help dialog ('?') and global keys                        */
  /* ---------------------------------------------------------------- */
  function initShortcuts() {
    var dialog = document.getElementById("shortcuts-dialog");
    var opener = null;
    function open(from) {
      if (!dialog || dialog.open) return;
      opener = from || document.activeElement;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (dialog) {
      dialog.addEventListener("close", function () { if (opener && opener.focus) opener.focus(); });
      dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
    }
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-shortcuts-open]");
      if (b) open(b);
    });
    document.addEventListener("keydown", function (e) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      if (e.key === "?") { e.preventDefault(); open(); return; }
      if (e.key === "/") {
        var target = document.querySelector("[data-slash-target]");
        if (target) {
          e.preventDefault();
          KSE.openFilters && KSE.openFilters();
          target.focus();
        }
      }
    });
  }

  /* ---------------------------------------------------------------- */
  /* Tabs — WAI-ARIA, automatic activation                             */
  /* ---------------------------------------------------------------- */
  function initTabs(scope) {
    (scope || document).querySelectorAll("[role=tablist][data-tabs]").forEach(function (list) {
      var tabs = Array.prototype.slice.call(list.querySelectorAll("[role=tab]"));
      // keep the active tab visible when the tab strip scrolls (narrow screens)
      function reveal(tab) {
        var lr = list.getBoundingClientRect(), tr = tab.getBoundingClientRect();
        if (tr.left < lr.left || tr.right > lr.right) list.scrollLeft += tr.left - lr.left - 16;
      }
      window.addEventListener("resize", function () {
        var cur = list.querySelector("[aria-selected=true]");
        if (cur) reveal(cur);
      });
      function select(tab, focus) {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          var panel = document.getElementById(t.getAttribute("aria-controls"));
          if (panel) panel.hidden = !on;
        });
        if (focus) tab.focus();
        reveal(tab);
        if (list.hasAttribute("data-tabs-hash")) {
          try { history.replaceState(null, "", "#" + tab.getAttribute("data-key")); } catch (e) {}
        }
        list.dispatchEvent(new CustomEvent("kse:tabchange", { bubbles: true, detail: { key: tab.getAttribute("data-key") } }));
      }
      tabs.forEach(function (tab, i) {
        tab.addEventListener("click", function () { select(tab, false); });
        tab.addEventListener("keydown", function (e) {
          var next = null;
          if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
          else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
          else if (e.key === "Home") next = tabs[0];
          else if (e.key === "End") next = tabs[tabs.length - 1];
          if (next) { e.preventDefault(); select(next, true); }
        });
      });
      if (list.hasAttribute("data-tabs-hash") && location.hash) {
        var wanted = tabs.filter(function (t) { return "#" + t.getAttribute("data-key") === location.hash; })[0];
        if (wanted) select(wanted, false);
      }
    });
  }
  KSE.initTabs = initTabs;

  /* ---------------------------------------------------------------- */
  /* Dual range + numeric range validation (9.5 inline error)           */
  /* ---------------------------------------------------------------- */
  function initRanges(scope) {
    (scope || document).querySelectorAll("[data-dual-range], [data-num-range]").forEach(function (box) {
      var loR = box.querySelector("[data-role=lo-range]");
      var hiR = box.querySelector("[data-role=hi-range]");
      var loN = box.querySelector("[data-role=lo-num]");
      var hiN = box.querySelector("[data-role=hi-num]");
      var err = box.querySelector(".field__error");
      var track = box.querySelector(".range2__track");
      var min = parseFloat(box.getAttribute("data-min") || "0");
      var max = parseFloat(box.getAttribute("data-max") || "100");

      function validate() {
        var lo = parseFloat(loN.value), hi = parseFloat(hiN.value);
        var bad = !isNaN(lo) && !isNaN(hi) && lo > hi;
        if (err) err.hidden = !bad;
        [loN, hiN].forEach(function (n) { n.setAttribute("aria-invalid", bad ? "true" : "false"); });
        box.toggleAttribute("data-invalid", bad);
        return !bad;
      }
      function paint() {
        if (!track) return;
        var lo = parseFloat(loR.value), hi = parseFloat(hiR.value);
        track.style.setProperty("--lo", ((lo - min) / (max - min)) * 100 + "%");
        track.style.setProperty("--hi", ((hi - min) / (max - min)) * 100 + "%");
      }
      if (loR && hiR) {
        loR.addEventListener("input", function () {
          if (parseFloat(loR.value) > parseFloat(hiR.value)) loR.value = hiR.value;
          loN.value = loR.value; paint(); validate();
          loN.dispatchEvent(new Event("change", { bubbles: true }));
        });
        hiR.addEventListener("input", function () {
          if (parseFloat(hiR.value) < parseFloat(loR.value)) hiR.value = loR.value;
          hiN.value = hiR.value; paint(); validate();
          hiN.dispatchEvent(new Event("change", { bubbles: true }));
        });
        // keep the thumb that was touched last on top so overlapping thumbs stay usable
        [loR, hiR].forEach(function (r) {
          r.addEventListener("pointerdown", function () { loR.style.zIndex = r === loR ? 3 : 2; hiR.style.zIndex = r === hiR ? 3 : 2; });
        });
      }
      [loN, hiN].forEach(function (n) {
        n.addEventListener("input", function () {
          if (loR && n === loN && loN.value !== "") loR.value = loN.value;
          if (hiR && n === hiN && hiN.value !== "") hiR.value = hiN.value;
          paint(); validate();
        });
      });
      validate();
    });
  }
  KSE.initRanges = initRanges;

  /* ---------------------------------------------------------------- */
  /* Sortable tables — display order only                              */
  /* ---------------------------------------------------------------- */
  function initSortableTables(scope) {
    (scope || document).querySelectorAll("table[data-sortable]").forEach(function (table) {
      var heads = Array.prototype.slice.call(table.querySelectorAll("thead th[data-key]"));
      heads.forEach(function (th) {
        var btn = th.querySelector(".th-sort");
        if (!btn) return;
        btn.addEventListener("click", function () {
          var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
          var dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
          heads.forEach(function (h) { h.setAttribute("aria-sort", "none"); });
          th.setAttribute("aria-sort", dir);
          var numeric = th.hasAttribute("data-numeric");
          var tbody = table.tBodies[0];
          var rows = Array.prototype.slice.call(tbody.rows);
          rows.sort(function (a, b) {
            var ca = a.cells[idx], cb = b.cells[idx];
            var va = ca.hasAttribute("data-sort") ? ca.getAttribute("data-sort") : ca.textContent.trim();
            var vb = cb.hasAttribute("data-sort") ? cb.getAttribute("data-sort") : cb.textContent.trim();
            var r;
            if (numeric) {
              var na = va === "" ? Infinity : parseFloat(va), nb = vb === "" ? Infinity : parseFloat(vb);
              // empty (NULL) always last regardless of direction
              if (na === Infinity || nb === Infinity) return (na === Infinity) - (nb === Infinity);
              r = na - nb;
            } else {
              r = va.localeCompare(vb, "ko");
            }
            return dir === "ascending" ? r : -r;
          });
          rows.forEach(function (row) { tbody.appendChild(row); });
          th.querySelectorAll(".th-sort__icon").forEach(function (ic) {
            ic.innerHTML = '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' +
              (dir === "ascending" ? "M8 13l4-4 4 4" : "M8 11l4 4 4-4") + '"/></svg>';
          });
          heads.forEach(function (h) {
            if (h !== th) h.querySelectorAll(".th-sort__icon").forEach(function (ic) {
              ic.innerHTML = '<svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></svg>';
            });
          });
        });
      });
    });
  }
  KSE.initSortableTables = initSortableTables;

  /* ---------------------------------------------------------------- */
  /* Tooltips: Esc dismisses the visible tooltip (WCAG 1.4.13)          */
  /* ---------------------------------------------------------------- */
  function initTooltips() {
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      var host = document.activeElement && document.activeElement.closest(".tip-host, .btn-wrap.has-tip");
      if (host) {
        var tip = host.querySelector(".tip");
        if (tip) tip.classList.add("is-dismissed");
      }
    });
    document.addEventListener("focusout", function (e) {
      var host = e.target.closest && e.target.closest(".tip-host, .btn-wrap.has-tip");
      if (host) host.querySelectorAll(".tip.is-dismissed").forEach(function (t) { t.classList.remove("is-dismissed"); });
    });
    // aria-disabled buttons must not act
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-blocked]");
      if (b) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  }

  /* ---------------------------------------------------------------- */
  /* Table ⇄ chart toggles ('표로 보기', 9.7)                           */
  /* ---------------------------------------------------------------- */
  function initViewToggles() {
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-view-toggle]");
      if (!b) return;
      var card = b.closest("[data-figure]");
      if (!card) return;
      var chart = card.querySelector("[data-view=chart]");
      var table = card.querySelector("[data-view=table]");
      var showTable = table.hidden;
      table.hidden = !showTable;
      chart.hidden = showTable;
      b.setAttribute("aria-pressed", showTable ? "true" : "false");
      b.querySelector("span").textContent = showTable ? b.getAttribute("data-label-chart") : b.getAttribute("data-label-table");
    });
  }

  /* ---------------------------------------------------------------- */
  /* Copy link                                                         */
  /* ---------------------------------------------------------------- */
  function initCopyLink() {
    document.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy-link]");
      if (!b) return;
      var url = location.href;
      var done = function () { KSE.toast(b.getAttribute("data-done") || "링크를 복사했습니다."); };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(done, function () { fallback(); });
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = url; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (err) { KSE.toast(url, { tone: "info", duration: 8000 }); }
        ta.remove();
      }
    });
  }

  /* ---------------------------------------------------------------- */
  /* Boot                                                              */
  /* ---------------------------------------------------------------- */
  function boot() {
    syncThemeButtons();
    initNav();
    initShortcuts();
    initTabs();
    initRanges();
    initSortableTables();
    initTooltips();
    initViewToggles();
    initCopyLink();
    document.querySelectorAll("[data-toast-demo]").forEach(function (b) {
      b.addEventListener("click", function () {
        KSE.toast(b.getAttribute("data-toast-demo"), {
          tone: b.getAttribute("data-tone") || "ok",
          action: b.hasAttribute("data-undo") ? { label: "되돌리기", onClick: function () { KSE.toast("되돌렸습니다.", { tone: "info" }); } } : null,
        });
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
