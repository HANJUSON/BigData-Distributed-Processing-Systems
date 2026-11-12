/* 저장된 테마를 첫 그림 전에 적용(깜빡임 방지). <head>에서 defer 없이 읽는다.
   CSP(script-src 'self')가 인라인 스크립트를 막으므로 별도 파일로 둔다. */
try {
  var t = localStorage.getItem("kse-theme");
  if (t === "light" || t === "dark") { document.documentElement.setAttribute("data-theme", t); }
} catch (e) { /* 저장소 접근이 막혀도 시스템 설정을 따른다 */ }
