/*
 * S1 장면 검색 — 조건 변경 → 300 ms 디바운스 → 결과 영역만 교체 (9.4, 9.12).
 * - 별도의 ‘검색’ 버튼 없음. 조건 패널의 스크롤·포커스는 그대로 둔다.
 * - 모든 조건은 URL 쿼리에 담는다(뒤로 가기·새로고침·링크 공유).
 * - 결과 계산은 서버(MySQL 프로시저)가 한다. 여기서는 HTML 조각만 바꾼다.
 */
(function () {
  "use strict";
  var KSE = window.KSE || {};
  var form = document.getElementById("search-form");
  var results = document.getElementById("results");
  if (!form || !results) return;

  /* ---------------- drawer (≤ 1024 px) ---------------- */
  var panel = document.getElementById("filters");
  // 여는 버튼은 결과 영역(#results) 안에 있어 검색할 때마다 innerHTML로 교체된다.
  // 그래서 요소에 직접 리스너를 붙이지 않고 document에서 위임으로 받는다.
  function openers() { return document.querySelectorAll("[data-filters-open]"); }
  var lastOpener = null;
  function isDrawer() { return window.matchMedia("(max-width: 1024px)").matches; }
  function openFilters(from) {
    if (!isDrawer()) return;
    lastOpener = from || document.activeElement;
    panel.classList.add("is-open");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    openers().forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
    var first = panel.querySelector("[data-slash-target]");
    if (first) first.focus();
  }
  function closeFilters() {
    if (!panel.classList.contains("is-open")) return;
    panel.classList.remove("is-open");
    panel.removeAttribute("role");
    panel.removeAttribute("aria-modal");
    openers().forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
    // 열 때 누른 버튼이 그 사이 교체됐으면 새 버튼으로 포커스를 돌려준다
    if (lastOpener && !lastOpener.isConnected) lastOpener = openers()[0] || null;
    if (lastOpener && lastOpener.focus) lastOpener.focus();
  }
  KSE.openFilters = openFilters;
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-filters-open]");
    if (opener) openFilters(opener);
    else if (e.target.closest("[data-filters-close]")) closeFilters();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && panel.classList.contains("is-open")) { e.preventDefault(); closeFilters(); }
  });

  /* ---------------- query ⇄ results ---------------- */
  /* 가림은 값의 집합(sp_search_scenes_ex p_occ). 기본 0·1·2면 URL에서 뺀다 */
  var occBoxes = Array.prototype.slice.call(form.querySelectorAll("input[name=occ]"));
  function occIsDefault() {
    var on = occBoxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }).sort();
    return on.join(",") === form.getAttribute("data-occ-default");
  }

  function queryString() {
    var data = new FormData(form);
    var params = new URLSearchParams();
    var skipOcc = occIsDefault();
    data.forEach(function (value, key) {
      if (value === "" || value == null) return;
      // defaults are left out of the URL to keep links short
      if (key === "dmin" && value === form.getAttribute("data-dmin")) return;
      if (key === "dmax" && value === form.getAttribute("data-dmax")) return;
      if (key === "sort" && value === form.getAttribute("data-sort-default")) return;
      if (key === "occ" && skipOcc) return;
      if (key === "page") return;
      params.append(key, value);
    });
    var s = params.toString();
    return s ? "?" + s : location.pathname;
  }

  var timer = null;
  var inflight = null;
  var slowTimer = null;

  function load(url, push) {
    if (inflight) inflight.abort();
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    inflight = ctrl;
    // skeleton only if slower than 300 ms (flicker prevention, 9.5)
    clearTimeout(slowTimer);
    slowTimer = setTimeout(function () {
      results.setAttribute("aria-busy", "true");
      var tpl = document.getElementById("tpl-loading");
      var body = results.querySelector("[data-results-body]");
      if (tpl && body) {
        body.innerHTML = tpl.innerHTML;
        // 이미 300 ms를 기다렸으므로 CSS 지연(.skeleton-delay) 없이 바로 보인다
        var g = body.querySelector(".skeleton-delay");
        if (g) g.classList.remove("skeleton-delay");
      }
    }, 300);
    fetch(url, { headers: { "X-Requested-With": "fetch" }, signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        // 서버가 그린 오류 상태(503, NR-4 문구)도 결과 영역으로 그대로 보여준다
        return r.text().then(function (html) { return { status: r.status, html: html }; });
      })
      .then(function (res) {
        var doc = new DOMParser().parseFromString(res.html, "text/html");
        var fresh = doc.getElementById("results");
        if (!fresh) throw new Error("HTTP " + res.status);
        results.innerHTML = fresh.innerHTML;
        // 서버가 그린 새 버튼은 aria-expanded="false"이므로 열린 패널 상태에 맞춘다
        if (panel.classList.contains("is-open")) {
          openers().forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
        }
        if (push) { history.pushState({ kse: true }, "", url); shownSearch = location.search; }
        KSE.initSortableTables && KSE.initSortableTables(results);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        var tpl = document.getElementById("tpl-error");
        if (tpl) {
          results.querySelector("[data-results-body]").innerHTML = tpl.innerHTML;
          var pre = results.querySelector(".state__details code");
          if (pre) pre.textContent = String(err && err.message ? err.message : err);
        }
      })
      .then(function () {
        // 중단된(앞선) 요청이 새 요청의 로딩 표시를 지우지 않게 한다
        if (inflight !== ctrl) return;
        inflight = null;
        clearTimeout(slowTimer);
        results.removeAttribute("aria-busy");
      });
  }

  function schedule() {
    if (form.querySelector("[data-invalid]")) return; // 입력 검증 실패 시 검색하지 않음
    clearTimeout(timer);
    timer = setTimeout(function () { load(queryString(), true); }, 300);
  }

  form.addEventListener("change", schedule);
  // 정렬 select는 결과 영역에 있고 form="search-form"으로 폼에 연결된다
  results.addEventListener("change", function (e) { if (e.target.form === form) schedule(); });
  form.addEventListener("input", function (e) {
    if (e.target.type === "number") schedule();
  });
  form.addEventListener("submit", function (e) { e.preventDefault(); schedule(); });

  // chip × and pagination: swap results in place, keep panel state
  results.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-chip-remove], .pager a, a.chips__clear, [data-retry]");
    if (!a) return;
    e.preventDefault();
    if (a.hasAttribute("data-retry")) { load(location.href, false); return; }
    // chip/pager links navigate fully for now so the form reflects the new state
    location.href = a.getAttribute("href");
  });

  // 뒤로·앞으로 가기로 검색 조건(쿼리)이 바뀐 경우만 다시 불러온다.
  // 같은 문서 안의 해시 이동(건너뛰기 링크 #main 등)도 popstate를 일으키므로 무시한다.
  var shownSearch = location.search;
  window.addEventListener("popstate", function () {
    if (location.search !== shownSearch) location.reload();
  });

  /* ---------------- 상세에서 돌아오면 스크롤 위치 복원 (9.4) ---------------- */
  var SCROLL_KEY = "kse-s1-scroll:" + location.search;
  results.addEventListener("click", function (e) {
    if (e.target.closest(".rcard__link")) {
      try { sessionStorage.setItem(SCROLL_KEY, String(window.scrollY)); } catch (err) {}
    }
  });
  try {
    var y = sessionStorage.getItem(SCROLL_KEY);
    if (y !== null) { window.scrollTo(0, parseInt(y, 10) || 0); sessionStorage.removeItem(SCROLL_KEY); }
  } catch (err) {}
})();
