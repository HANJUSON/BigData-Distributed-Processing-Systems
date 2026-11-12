"""웹 앱 기본 시험 (README 9.4·9.6·9.8, U-2).

- 모든 화면이 200을 돌려주고 공통 랜드마크(header/nav/main/footer)가 있다.
- 바닥글에 데이터 출처·라이선스 문구가 있다(D-4).
- 테마 전환 버튼·단축키 대화상자·건너뛰기 링크가 있다.
- tokens.css 밖(app.css·템플릿)에 색상 값 직접 사용 0건(U-2 grep 기준).
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from webapp import create_app
from webapp.i18n import ko

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"

PAGES = {
    "/": "pages.search",
    "/?cls=Pedestrian&dmax=30&occ=1&low_obs=1&page=2": "pages.search",
    "/scenes/123": "pages.search",  # S2는 ‘장면 검색’ 메뉴 아래
    "/scenes/7480": "pages.search",
    "/review-sets": "pages.review_sets",
    "/dashboard": "pages.dashboard",
    "/pipeline": "pages.pipeline",
    "/about": "pages.about",
}

HEX_COLOR = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")
COLOR_FUNC = re.compile(r"(?<![a-z-])(?:rgba?|hsla?)\(")


@pytest.fixture
def client():
    # 레이아웃 시험은 데모 데이터로 한다(MySQL·원본 없이). 실제 데이터 연결은 test_webapp_data.py.
    app = create_app({"TESTING": True, "KITTI_DEMO": True})
    return app.test_client()


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize("url", list(PAGES))
def test_every_route_returns_200(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"


@pytest.mark.parametrize("url", list(PAGES))
def test_base_landmarks(client, url):
    html = client.get(url).get_data(as_text=True)
    assert '<html lang="ko">' in html
    assert re.search(r"<header[^>]*class=\"site-header\"", html)
    assert re.search(r"<nav[^>]*aria-label=\"주 메뉴\"", html)
    assert '<main id="main"' in html
    assert re.search(r"<footer[^>]*class=\"site-footer\"", html)
    assert html.count("<main") == 1
    # 건너뛰기 링크가 본문을 가리킨다
    assert 'class="skip-link" href="#main"' in html


@pytest.mark.parametrize("url, endpoint", PAGES.items())
def test_nav_has_five_items_and_one_current(client, url, endpoint):
    html = client.get(url).get_data(as_text=True)
    nav = re.search(r'<nav class="primary-nav".*?</nav>', html, re.S).group(0)
    links = re.findall(r"<a [^>]*primary-nav__link[^>]*>(.*?)</a>", nav)
    assert links == [item["label"] for item in ko.NAV_ITEMS]
    assert nav.count('aria-current="page"') == 1
    current = re.search(r'<a href="([^"]+)" class="primary-nav__link" aria-current="page">', nav)
    with create_app({"TESTING": True}).test_request_context():
        from flask import url_for
        assert current.group(1) == url_for(endpoint)


@pytest.mark.parametrize("url", list(PAGES))
def test_footer_license(client, url):
    html = client.get(url).get_data(as_text=True)
    footer = re.search(r"<footer.*?</footer>", html, re.S).group(0)
    assert ko.LICENSE_LINE in _text(footer)
    assert "creativecommons.org/licenses/by-nc-sa/3.0" in footer


def test_theme_toggle_and_dialog_markup(client):
    html = client.get("/").get_data(as_text=True)
    assert "data-theme-toggle" in html
    assert 'aria-label="다크 테마로 전환"' in html
    # 저장된 테마를 첫 그림 전에 적용(head의 동기 외부 스크립트, CSP 때문에 인라인 아님)하고, 저장소 오류는 삼킨다
    head = html.split("</head>", 1)[0]
    theme = re.search(r'<script src="/static/js/theme\.js\?v=[^"]+"></script>', head)
    assert theme and head.index(theme.group(0)) < head.index('rel="stylesheet"')
    js = (WEBAPP / "static/js/theme.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("kse-theme")' in js and "try" in js and "catch (e)" in js
    # 단축키 도움말은 네이티브 <dialog>
    assert '<dialog class="dialog" id="shortcuts-dialog" aria-labelledby="shortcuts-title">' in html
    # 검수자 선택은 비활성 + 이유 설명
    assert re.search(r'<select id="reviewer-select"[^>]*disabled[^>]*aria-describedby="reviewer-why"', html)
    assert ko.UI["reviewer_disabled"] in html
    # 알림 영역
    assert 'id="toast-region" role="status" aria-live="polite"' in html


@pytest.mark.parametrize("url", list(PAGES) + ["/nope-404"])
def test_security_headers(client, url):
    resp = client.get(url)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "'unsafe-eval'" not in csp
    # CSP와 맞는지: 실행되는 인라인 스크립트·이벤트 처리기 속성·javascript: URL이 없어야 한다
    html = resp.get_data(as_text=True)
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, tag
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert "javascript:" not in html


def test_security_headers_on_static_files(client):
    resp = client.get("/static/js/app.js")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in resp.headers


def test_theme_js_guards_storage():
    js = (WEBAPP / "static/js/app.js").read_text(encoding="utf-8")
    assert "localStorage" in js and "try" in js and "catch" in js
    assert "kse-theme" in js


def test_tokens_define_both_themes_and_classes():
    css = (WEBAPP / "static/css/tokens.css").read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in css
    assert '[data-theme="dark"]' in css and '[data-theme="light"]' in css
    expected = {
        "--bg": ("#FFFFFF", "#0F1115"), "--surface": ("#F6F7F9", "#171A21"),
        "--border": ("#E3E6EB", "#2A2F3A"), "--text": ("#14171F", "#E8EAEE"),
        "--text-muted": ("#5B6372", "#9AA3B2"), "--accent": ("#2457D6", "#6E9BFF"),
        "--warn": ("#9E5300", "#F0A63A"), "--danger": ("#C62828", "#FF6B6B"),
        "--ok": ("#1E7F4F", "#4CC38A"),
    }
    for token, (light, dark) in expected.items():
        assert f"{token}: {light};" in css, token
        assert f"{token}: {dark};" in css, token
    for slug, hex_ in {"car": "#0072B2", "van": "#56B4E9", "truck": "#E69F00",
                       "pedestrian": "#D55E00", "person-sitting": "#F0E442",
                       "cyclist": "#009E73", "tram": "#CC79A7", "misc": "#8C8C8C"}.items():
        assert f"--cls-{slug}: {hex_};" in css
    for size in (12, 14, 16, 20, 24, 32):
        assert f"--fs-{size}: {size}px;" in css
    assert "--radius: 6px;" in css


def _files_outside_tokens():
    yield WEBAPP / "static/css/app.css"
    yield from sorted((WEBAPP / "templates").rglob("*.html"))


@pytest.mark.parametrize("path", list(_files_outside_tokens()), ids=lambda p: p.name)
def test_no_raw_colors_outside_tokens(path):
    """U-2: 화면 코드에 색상 값 직접 사용 0건."""
    text = path.read_text(encoding="utf-8")
    # 색 형식(#rgb, #rgba, #rrggbb, #rrggbbaa)만 센다. HTML 엔티티(&#123;)는 앞의 '&'로 제외.
    found = [m.group(0) for m in HEX_COLOR.finditer(text) if len(m.group(0)) - 1 in (3, 4, 6, 8)]
    assert found == [], f"{path.name}: raw colour literal(s) {found}"
    assert not COLOR_FUNC.search(text), f"{path.name}: rgb()/hsl() must live in tokens.css"


def test_component_gallery_only_in_debug():
    assert create_app({"TESTING": True}).test_client().get("/_components").status_code == 404
    app = create_app({"TESTING": True, "DEBUG": True})
    resp = app.test_client().get("/_components")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'data-theme="light"' in html and 'data-theme="dark"' in html


def test_demo_data_is_labelled(client):
    # S5는 데모 경로 없이 항상 결과 파일을 읽는다(test_webapp_data.py).
    for url in ("/", "/scenes/123"):
        assert ko.UI["demo_badge"] in client.get(url).get_data(as_text=True), url


def test_points_endpoint_is_float32_binary(client):
    resp = client.get("/scenes/123/points.bin")
    assert resp.status_code == 200
    assert resp.mimetype == "application/octet-stream"
    data = resp.data
    assert len(data) % 16 == 0 and len(data) > 0
    x, y, z, r = struct.unpack_from("<4f", data, 0)
    assert 0.0 <= x <= 80.0 and -40.0 <= y <= 40.0


def test_search_state_round_trips_into_chips(client):
    # sp_search_scenes 인자: 클래스 하나, 최대 거리, 가림 연속 범위
    html = client.get("/?cls=Pedestrian&dmax=30&occ=1").get_data(as_text=True)
    assert "보행자 조건 해제" in html
    assert "거리 ≤ 30 m" in html
    assert "일부 가림" in html
    # 정렬 select가 폼에 연결되어 URL 동기화에 포함된다
    assert 'form="search-form"' in html


def test_s2_null_points_and_low_obs_are_not_color_only(client):
    html = client.get("/scenes/123").get_data(as_text=True)
    assert ko.UI["points_null"] in html
    assert "관측 부족" in html and "⚠" in html


def test_s6_definitions(client):
    text = _text(client.get("/about").get_data(as_text=True))
    assert "√(x² + z²)" in text
    for label in ko.OCCLUSION_LABELS.values():
        assert label in text
    for label in ko.CLASS_LABELS.values():
        assert label in text
    assert "CC BY-NC-SA 3.0" in text and "하위 10%" in text


def test_db_course_placeholders(client):
    for url in ("/review-sets", "/dashboard"):
        html = client.get(url).get_data(as_text=True)
        assert ko.UI["db_course_notice"] in html


def test_404_page_is_designed(client):
    resp = client.get("/no-such-page")
    assert resp.status_code == 404
    assert ko.STATE_MESSAGES["not_found_title"] in resp.get_data(as_text=True)


def test_vendor_assets_and_licenses_present():
    for rel in (
        "static/fonts/pretendard/Pretendard-Regular.woff2",
        "static/fonts/pretendard/Pretendard-Bold.woff2",
        "static/fonts/pretendard/LICENSE.txt",
        "static/fonts/jetbrains-mono/jetbrains-mono-latin-400-normal.woff2",
        "static/fonts/jetbrains-mono/LICENSE",
        "static/vendor/chartjs/chart.umd.min.js",
        "static/vendor/chartjs/LICENSE.md",
    ):
        assert (WEBAPP / rel).is_file(), rel


def test_css_js_budget():
    """9.9: CSS·JS 합계 300 KB 이하(가장 무거운 S5 기준: Chart.js 포함)."""
    files = ["static/css/tokens.css", "static/css/app.css", "static/js/app.js",
             "static/js/pipeline.js", "static/vendor/chartjs/chart.umd.min.js"]
    total = sum((WEBAPP / f).stat().st_size for f in files)
    assert total <= 300 * 1024, total
