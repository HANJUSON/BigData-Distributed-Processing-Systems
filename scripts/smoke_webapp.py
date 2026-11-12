"""웹 앱 실제 데이터 점검 (README F-10, U-4·U-7·U-11, 9.9 성능 기준).

실제 MySQL(~/.my.cnf 또는 KITTI_MYSQL_CNF)·KITTI 원본·썸네일·results/ 파일로 Flask 앱을
test client로 띄우고 다음을 확인한다. 단위 시험(pytest)은 가짜 DB를 쓰므로 이 스크립트가
실제 연결의 증거다.

1. S1: 화면에 보이는 장면 목록·일치 객체 수·결과 수가 같은 인자의 ``CALL sp_search_scenes_ex(…)``와 같다(U-4).
   - T1 “30 m 이상 · 일부 가림 · 보행자”는 gt_object·object_feature에 대한 독립 SQL 집계와도 같다.
   - 정렬 4가지 각각 화면 순서 = CALL 순서이고, 정렬 키가 실제로 단조롭다.
   - 불변식: 기본 인자로 줄였을 때 sp_search_scenes_ex = sp_search_scenes (DB 과목 프로시저, 행·순서).
   - 빈 결과 완화 제안 수도 해당 조건을 푼 CALL의 행 수와 같다.
2. S2: 장면 0·1000·5000의 객체 표가 ``CALL sp_scene_detail``과 같고, 이미지·점군·BEV 박스가 나온다.
3. S5: 한 줄 결론과 그림·파일 링크가 결과 파일과 맞는다.
4. 썸네일·points.bin 경로와 캐시 헤더.
5. 서버 쪽 성능: S1 응답 시간(30회) p50·p95, S2 HTML·이미지·점군 응답 시간과 용량.

실행::

    .venv/bin/python scripts/smoke_webapp.py [--requests 30]

종료 코드: 0 = 모든 점검 통과, 1 = 실패 있음, 2 = DB에 연결하지 못함.
측정은 같은 프로세스의 test client로 한다(네트워크·브라우저 그리기 시간 제외).
이 스크립트는 점검용이라 독립 대조 SQL을 직접 실행한다(웹 앱 코드는 고정된 CALL만 쓴다).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

import pymysql
import pymysql.cursors

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp import create_app  # noqa: E402
from webapp.db import DBError, MySQLDatabase  # noqa: E402
from webapp.service import PAGE_SIZE  # noqa: E402

ALL = 100_000  # 전체 행을 받을 때의 p_limit

# (설명, 쿼리 문자열, sp_search_scenes_ex 필터 8개 인자, 정렬) — 인자는 화면과 독립적으로 손으로 적었다.
#   필터 인자: p_classes, p_min_dist, p_max_dist, p_occ, p_min_points, p_max_points, p_low_only, p_unreviewed
S1_CASES = [
    ("기본 (전체, 가림 0–2)", "", (None, None, None, None, None, None, 0, 0), "match"),
    ("T1: 30 m 이상 · 일부 가림 · 보행자", "cls=Pedestrian&dmin=30&occ=1",
     ("[4]", 30, None, "[1]", None, None, 0, 0), "match"),
    ("보행자+자전거 · 10–40 m · 가림 {0,2} · 관측 부족만 · 2쪽",
     "cls=Pedestrian&cls=Cyclist&dmin=10&dmax=40&occ=0&occ=2&low_obs=1&page=2",
     ("[4, 6]", 10, 40, "[0, 2]", None, None, 1, 0), "match"),
    ("차량 · 포인트 20–200 · 가림 0–3 · 미검수만", "cls=Car&pmin=20&pmax=200&occ=0&occ=1&occ=2&occ=3&unreviewed=1",
     ("[1]", None, None, "[0, 1, 2, 3]", 20, 200, 0, 1), "match"),
]
T1_INDEPENDENT_SQL = """
SELECT COUNT(DISTINCT o.frame_id) AS scenes, COUNT(*) AS objects
FROM gt_object o
JOIN object_class c   ON c.class_id = o.class_id
JOIN object_feature f ON f.frame_id = o.frame_id AND f.object_id = o.object_id
WHERE c.class_name = 'Pedestrian'
  AND SQRT(o.loc_x * o.loc_x + o.loc_z * o.loc_z) >= 30
  AND o.occluded = 1
  AND f.points_in_box IS NOT NULL
"""
SORT_QUERY = ("보행자 · 10 m 이상", "cls=Pedestrian&dmin=10", ("[4]", 10, None, None, None, None, 0, 0))
SORT_KEYS = {  # 정렬 → (CALL 행에서 키, 방향) — 동점은 frame_id 오름차순
    "match": (lambda r: (-int(r["matched_objects"]), int(r["frame_id"]))),
    "far": (lambda r: (-float(r["farthest_m"]), int(r["frame_id"]))),
    "few_points": (lambda r: (int(r["min_points"]), int(r["frame_id"]))),
    "frame": (lambda r: (int(r["frame_id"]),)),
}
# 불변식: (sp_search_scenes 인자 7개, 같은 뜻의 sp_search_scenes_ex 필터 인자 8개)
BASE_EQUIV = [
    ((None, None, 0, 2, None, 0, 0), (None, None, None, None, None, None, 0, 0)),
    ((4, 20, 0, 3, None, 1, 0), ("[4]", None, 20, "[0, 1, 2, 3]", None, None, 1, 0)),
    ((1, None, 1, 1, 50, 0, 0), ("[1]", None, None, "[1]", None, 50, 0, 0)),
    ((6, 30, 0, 2, None, 0, 1), ("[6]", None, 30, "[0, 1, 2]", None, None, 0, 1)),
    ((3, 45.5, 2, 3, 100, 1, 0), ("[3]", None, 45.5, "[2, 3]", None, 100, 1, 0)),
]
EMPTY_CASE = ("트램 · 5 m 이내 · 관측 부족만 (빈 결과)", "cls=Tram&dmax=5&low_obs=1",
              ("[7]", None, 5, None, None, None, 1, 0))
RELAX_LABEL = {"class": "객체 종류", "dist": "거리", "occ": "가림", "points": "포인트 수",
               "low_only": "관측 부족만", "unreviewed": "미검수만"}
PERF_QUERIES = [q for _, q, _, _ in S1_CASES] + [
    "cls=Pedestrian&dmin=30&occ=1&sort=far", "cls=Van&low_obs=1&sort=few_points", "dmax=15&sort=frame",
    "cls=Truck&cls=Tram&occ=2", "pmax=10", "cls=Misc&dmin=20&dmax=60&occ=0&occ=1", EMPTY_CASE[1],
]
S2_FRAMES = [0, 1000, 5000]

failures: list[str] = []


def check(ok: bool, label: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        failures.append(label)
    return ok


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def parse_cards(html: str) -> list[tuple[int, int]]:
    """결과 카드 (frame_id, 일치 객체 수) — 화면에 보이는 순서대로."""
    cards = []
    for art in re.findall(r'<article class="rcard">(.*?)</article>', html, re.S):
        fid = int(re.search(r'<span class="mono">(\d{6})</span>', art).group(1))
        meta = re.search(r'<p class="rcard__meta">(.*?)</p>', art, re.S).group(1)
        n = int(re.search(r'<span class="num">([\d,]+)</span>', meta).group(1).replace(",", ""))
        cards.append((fid, n))
    return cards


def parse_counts(html: str) -> tuple[int, int]:
    h = re.search(r'id="results-count"[^>]*>(.*?)</h2>', html, re.S).group(1)
    nums = [int(x.replace(",", "")) for x in re.findall(r'<span class="num">([\d,]+)</span>', h)]
    return nums[0], nums[1]


def relaxed(params: tuple, cond: str) -> tuple:
    """sp_search_summary의 완화 정의: 그 조건 하나만 기본값으로 되돌린다."""
    p = list(params)
    if cond == "class":
        p[0] = None
    elif cond == "dist":
        p[1] = p[2] = None
    elif cond == "occ":
        p[3] = None
    elif cond == "points":
        p[4] = p[5] = None
    elif cond == "low_only":
        p[6] = 0
    elif cond == "unreviewed":
        p[7] = 0
    return tuple(p)


class Raw:
    """점검용 직접 연결: 독립 대조 SQL과 DB 과목 프로시저 sp_search_scenes 호출."""

    def __init__(self, cnf: Path) -> None:
        self.conn = pymysql.connect(read_default_file=str(cnf), read_default_group="client",
                                    cursorclass=pymysql.cursors.DictCursor, autocommit=True, connect_timeout=3)

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            out = list(cur.fetchall())
            while cur.nextset():
                pass
            return out

    def close(self) -> None:
        self.conn.close()


def ex(db: MySQLDatabase, params: tuple, sort: str = "match", limit: int = ALL, offset: int = 0) -> list[dict]:
    return db.call("sp_search_scenes_ex", *params, sort, limit, offset)[0]


# ---------------------------------------------------------------------------
def smoke_s1(client, db: MySQLDatabase, raw: Raw) -> None:  # noqa: ANN001
    print("\n== S1 장면 검색: 화면 ↔ CALL sp_search_scenes_ex (U-4)")
    for label, qs, params, sort in S1_CASES:
        resp = client.get("/?" + qs if qs else "/")
        html = resp.get_data(as_text=True)
        page = int(re.search(r"page=(\d+)", qs).group(1)) if "page=" in qs else 1
        rows = ex(db, params, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE)
        everything = ex(db, params, sort)
        shown = parse_cards(html)
        expected = [(int(r["frame_id"]), int(r["matched_objects"])) for r in rows]
        scenes, objects = parse_counts(html)
        print(f"- {label}: CALL sp_search_scenes_ex{params + (sort, PAGE_SIZE, (page - 1) * PAGE_SIZE)}")
        check(resp.status_code == 200, f"HTTP 200 ({resp.status_code})")
        check(shown == expected and len(shown) > 0,
              f"카드 {len(shown)}개의 장면·일치 객체 수·순서가 CALL 결과와 같음 "
              f"(앞 3개 화면 {shown[:3]} / CALL {expected[:3]})")
        check(scenes == len(everything), f"결과 수 ‘장면 {scenes:,}개’ = CALL 전체 행 수 {len(everything):,}")
        check(objects == sum(int(r["matched_objects"]) for r in everything),
              f"‘객체 {objects:,}개’ = CALL matched_objects 합")
        if label.startswith("T1"):
            ind = raw.rows(T1_INDEPENDENT_SQL)[0]
            check((int(ind["scenes"]), int(ind["objects"])) == (scenes, objects),
                  f"T1 독립 SQL(gt_object·object_feature, √(x²+z²) ≥ 30, occluded = 1): "
                  f"장면 {int(ind['scenes']):,} · 객체 {int(ind['objects']):,} = 화면")
        if shown:
            fid, _ = shown[0]
            idx = (page - 1) * PAGE_SIZE + 1
            link = re.search(rf'href="(/scenes/{fid}\?[^"]*i={idx})"', html)
            if check(link is not None, f"첫 카드 링크에 검색 조건과 순번 i={idx}"):
                s2 = client.get(link.group(1).replace("&amp;", "&")).get_data(as_text=True)
                check(f"({idx:,} / {scenes:,})" in text(s2), f"S2 위치 표시 ({idx:,} / {scenes:,})")

    label, qs, params = SORT_QUERY
    print(f"\n== S1 정렬 4가지: 화면 순서 = CALL 순서 ({label})")
    for sort in ("match", "far", "few_points", "frame"):
        for page in (1, 2):
            html = client.get(f"/?{qs}&sort={sort}&page={page}").get_data(as_text=True)
            want = [(int(r["frame_id"]), int(r["matched_objects"]))
                    for r in ex(db, params, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE)]
            check(parse_cards(html) == want and len(want) == PAGE_SIZE,
                  f"sort={sort} {page}쪽: 화면 {[f for f, _ in parse_cards(html)][:5]}… = CALL {[f for f, _ in want][:5]}…")
        full = ex(db, params, sort)
        key = SORT_KEYS[sort]
        check([key(r) for r in full] == sorted(key(r) for r in full),
              f"sort={sort}: 전체 {len(full):,}행이 정렬 키 순서(동점은 frame_id)")
        check(sorted(int(r["frame_id"]) for r in full) == sorted(int(r["frame_id"]) for r in ex(db, params)),
              f"sort={sort}: 정렬만 다르고 장면 집합은 같음")

    print("\n== 불변식: 기본 인자로 줄인 sp_search_scenes_ex = sp_search_scenes (행·순서)")
    for base, exp in BASE_EQUIV:
        b = raw.rows("CALL sp_search_scenes(%s, %s, %s, %s, %s, %s, %s, %s, %s)", base + (ALL, 0))
        e = ex(db, exp)
        bt = [(int(r["frame_id"]), int(r["matched_objects"]), r["min_points"]) for r in b]
        et = [(int(r["frame_id"]), int(r["matched_objects"]), r["min_points"]) for r in e]
        check(bt == et and len(bt) > 0, f"CALL sp_search_scenes{base} {len(bt):,}행 = sp_search_scenes_ex{exp} {len(et):,}행")

    label, qs, params = EMPTY_CASE
    print(f"\n- {label}")
    html = client.get("/?" + qs).get_data(as_text=True)
    total = len(ex(db, params))
    check(total == 0 and "조건에 맞는 장면이 없습니다" in html, f"빈 결과 상태 (CALL 행 수 {total})")
    relax = db.call("sp_search_summary", *params)[1]
    best = relax[0]
    count = len(ex(db, relaxed(params, best["cond"])))
    msg = f"{RELAX_LABEL[best['cond']]} 조건을 해제하면 {count:,}개가 있습니다."
    check(msg in html, f"완화 제안 ‘{msg}’ = 그 조건을 푼 CALL sp_search_scenes_ex 행 수")
    for _, _, params, _ in S1_CASES[1:] + [(None, None, params, None)]:
        for r in db.call("sp_search_summary", *params)[1]:
            n = len(ex(db, relaxed(params, r["cond"])))
            check(int(r["scenes"]) == n, f"  sp_search_summary{params} 완화 {r['cond']}: {int(r['scenes']):,} = CALL 행 수 {n:,}")


def smoke_s2(client, db: MySQLDatabase, sizes: dict) -> None:  # noqa: ANN001
    print("\n== S2 장면 상세: 화면 ↔ CALL sp_scene_detail, 원본 이미지·점군")
    for fid in S2_FRAMES:
        resp = client.get(f"/scenes/{fid}")
        html = resp.get_data(as_text=True)
        objs = db.call("sp_scene_detail", fid)[1]
        real = [o for o in objs if not o["is_ignore"]]
        print(f"- 장면 {fid:06d}: 객체 {len(real)}개, 무시 영역 {len(objs) - len(real)}개")
        check(resp.status_code == 200, "HTTP 200")
        rows = [int(x) for x in re.findall(r'<tr data-obj="(\d+)"', html)]
        check(rows == [int(o["object_id"]) for o in real], f"객체 표 번호 {rows} = CALL 객체 번호(무시 영역 제외)")
        cells = re.findall(r'<tr data-obj="\d+".*?</tr>', html, re.S)
        pts_ok = True
        for o, cell in zip(real, cells):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", cell, re.S)
            shown = text(tds[5]).strip()
            want = "계산 안 함(3D 박스 무효)" if o["points_in_box"] is None else f"{int(o['points_in_box']):,}"
            pts_ok &= want in shown and (("⚠" in shown) == bool(o["is_low"]))
        check(pts_ok, "포인트 수·NULL 표시·관측 부족 ⚠가 CALL 값과 같음")
        data = json.loads(re.search(r'id="scene-data">(.*?)</script>', html, re.S).group(1))
        n_valid = sum(1 for o in real if o["valid_3d"])
        check(len(data["footprints"]) == n_valid, f"BEV 박스 {len(data['footprints'])}개 = 유효 3D 객체 {n_valid}개")
        img = client.get(f"/scenes/{fid}/image.png")
        pts = client.get(f"/scenes/{fid}/points.bin")
        check(img.status_code == 200 and img.mimetype == "image/png", f"이미지 {len(img.data):,} B")
        check(pts.status_code == 200 and len(pts.data) % 16 == 0 and len(pts.data) > 0,
              f"점군(ROI) {len(pts.data) // 16:,}점 = {len(pts.data):,} B (float32 × 4)")
        sizes.setdefault("s2_html", []).append(len(resp.data))
        sizes.setdefault("image", []).append(len(img.data))
        sizes.setdefault("points", []).append(len(pts.data))


def smoke_s5(client) -> None:  # noqa: ANN001
    print("\n== S5 처리 결과: 결과 파일 ↔ 화면 (U-7)")
    res = ROOT / "results"
    html = client.get("/pipeline").get_data(as_text=True)
    t = text(html)
    q = json.loads((res / "quality_report.json").read_text(encoding="utf-8"))
    a = json.loads((res / "analysis_summary.json").read_text(encoding="utf-8"))
    m = json.loads((res / "mysql_consistency.json").read_text(encoding="utf-8"))
    comp = q["completeness"]
    check(f"{comp['n_expected']:,}개 장면 중 {comp['n_linked']:,}개 연결" in t, "품질 결론 = quality_report.json")
    check(f"객체 {a['f9']['n_low_objects']:,}개, 장면 {a['f9']['n_low_scenes']:,}개" in t, "관측 부족 결론 = analysis_summary.json")
    check(f"{m['summary']['n_ok']} / {m['summary']['n_checks']}" in t,
          f"MySQL 적재 대조 {m['summary']['n_ok']}/{m['summary']['n_checks']}")
    for scale, rel in ((100, "subsets/sample_100"), (1000, "subsets/sample_1000"), (7481, ".")):
        v = json.loads((res / rel / "verification.json").read_text(encoding="utf-8"))
        check(v["pass"] and v["n_items"] == 13, f"F-11 대조 {scale:,}개 장면: {v['n_items']}개 항목 pass={v['pass']}")
    table = re.search(r'id="fig-accuracy".*?</table>', html, re.S).group(0)
    check(table.count("통과") == 39, f"대조 표 통과 표시 {table.count('통과')}개 = 13 × 3")
    bench = (res / "bench" / "summary.csv").exists()
    check(bench or ("측정 전입니다" in t and 'id="chart-speedup"' not in html),
          "성능: results/bench 없음 → ‘측정 전’ (숫자 없음)" if not bench else "성능: bench/summary.csv 표시")
    figs = re.findall(r'src="(/pipeline/files/figures/[^"]+\.png)"', html)
    codes = {f: client.get(f).status_code for f in figs}
    check(len(figs) >= 7 and all(c == 200 for c in codes.values()), f"그림 {len(figs)}개 모두 200")
    links = re.findall(r'href="(/pipeline/files/[^"]+)" download', html)
    check(links and all(client.get(h).status_code == 200 for h in links), f"원본 파일 링크 {len(links)}개 모두 200")


def smoke_thumbs(client, sizes: dict) -> None:  # noqa: ANN001
    print("\n== 썸네일·경로 안전성")
    for fid in (0, 1000, 5000, 7480):
        r = client.get(f"/thumbs/{fid}.webp")
        ok = r.status_code == 200 and r.mimetype == "image/webp" and "immutable" in r.headers.get("Cache-Control", "")
        check(ok and len(r.data) <= 30 * 1024, f"/thumbs/{fid}.webp {len(r.data):,} B, {r.headers.get('Cache-Control')}")
        sizes.setdefault("thumb", []).append(len(r.data))
    for bad in ("/thumbs/7481.webp", "/thumbs/..%2F..%2Fetc%2Fpasswd", "/scenes/7481/points.bin",
                "/pipeline/files/../README.md"):
        check(client.get(bad).status_code == 404, f"{bad} → 404")


def perf(client, n: int, sizes: dict) -> dict:  # noqa: ANN001
    print(f"\n== 성능 (서버 쪽, test client, S1 {n}회)")
    client.get("/")  # 예열
    s1 = []
    for i in range(n):
        q = PERF_QUERIES[i % len(PERF_QUERIES)]
        t0 = time.perf_counter()
        r = client.get("/?" + q, headers={"X-Requested-With": "fetch"})
        s1.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    s2_html, s2_ctx, s2_img, s2_pts = [], [], [], []
    # 검색 결과에서 연 경우: 위치(12 / 1,284)·이전·다음을 위해 프로시저 2개를 더 부른다
    listing = client.get("/?" + S1_CASES[1][1]).get_data(as_text=True)
    ctx_links = [h.replace("&amp;", "&") for h in re.findall(r'class="rcard__link" href="([^"]+)"', listing)][:9]
    for href in ctx_links:
        t0 = time.perf_counter(); r = client.get(href); s2_ctx.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200 and "data-nav-next" in r.get_data(as_text=True)
    for fid in S2_FRAMES * 3:
        t0 = time.perf_counter(); client.get(f"/scenes/{fid}"); s2_html.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter(); client.get(f"/scenes/{fid}/image.png"); s2_img.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter(); client.get(f"/scenes/{fid}/points.bin"); s2_pts.append((time.perf_counter() - t0) * 1000)
    common = ("css/tokens.css", "css/app.css", "js/app.js")
    pages = {"S1": common + ("js/search.js",), "S2": common + ("js/scene.js",),
             "S5": common + ("js/pipeline.js", "vendor/chartjs/chart.umd.min.js")}
    static = {k: sum((ROOT / "webapp/static" / f).stat().st_size for f in v) for k, v in pages.items()}
    out = {
        "s1_ms": {"n": n, "p50": statistics.median(s1), "p95": pct(s1, 95), "max": max(s1)},
        "s2_html_ms": {"p50": statistics.median(s2_html), "p95": pct(s2_html, 95)},
        "s2_html_with_position_ms": {"p50": statistics.median(s2_ctx), "p95": pct(s2_ctx, 95)},
        "s2_image_ms": {"p50": statistics.median(s2_img), "p95": pct(s2_img, 95)},
        "s2_points_ms": {"p50": statistics.median(s2_pts), "p95": pct(s2_pts, 95)},
        "bytes": {k: {"min": min(v), "max": max(v)} for k, v in sizes.items()},
        "css_js_bytes_per_page": static,
    }
    print(f"  S1 응답 p50 {out['s1_ms']['p50']:.0f} ms · p95 {out['s1_ms']['p95']:.0f} ms · 최대 {out['s1_ms']['max']:.0f} ms"
          f"  (목표 p95 ≤ 500 ms)")
    first = out["s2_html_with_position_ms"]["p95"] + out["s2_image_ms"]["p95"]
    print(f"  S2 HTML p50 {out['s2_html_ms']['p50']:.0f} ms · p95 {out['s2_html_ms']['p95']:.0f} ms, "
          f"검색 위치 포함 p50 {out['s2_html_with_position_ms']['p50']:.0f} ms · p95 "
          f"{out['s2_html_with_position_ms']['p95']:.0f} ms, 이미지 p95 {out['s2_image_ms']['p95']:.0f} ms"
          f" → 첫 표시(서버, 위치 포함) ≈ {first:.0f} ms (목표 ≤ 1,000 ms)")
    print(f"  점군 전송(서버) p50 {out['s2_points_ms']['p50']:.0f} ms · p95 {out['s2_points_ms']['p95']:.0f} ms"
          f" (목표 BEV 전송+그리기 ≤ 1,500 ms, 그리기는 브라우저 측정 필요)")
    for k, v in out["bytes"].items():
        print(f"  용량 {k}: {v['min']:,}–{v['max']:,} B")
    print("  CSS·JS 페이지별 합 " + ", ".join(f"{k} {v:,} B" for k, v in static.items())
          + " (목표 페이지당 ≤ 300 KB = 307,200 B)")
    check(out["s1_ms"]["p95"] <= 500, "S1 p95 ≤ 500 ms")
    check(first <= 1000, "S2 첫 표시(HTML + 이미지, 서버) ≤ 1,000 ms")
    check(max(sizes["thumb"]) <= 30 * 1024, "썸네일 ≤ 30 KB")
    check(max(static.values()) <= 300 * 1024, "CSS·JS 페이지당 ≤ 300 KB")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--requests", type=int, default=30, help="S1 성능 측정 요청 수")
    ap.add_argument("--json", type=Path, default=None, help="측정값을 JSON으로 저장할 경로")
    args = ap.parse_args(argv)

    db = MySQLDatabase()
    try:
        n = len(db.call("sp_thresholds")[0])
    except DBError as exc:
        print(f"DB 연결 실패({exc.kind}): {exc.detail}", file=sys.stderr)
        return 2
    print(f"MySQL 연결: {db.cnf} · analysis_threshold {n}행")
    app = create_app({"KITTI_DEMO": False})
    print(f"원본: {app.config['KITTI_TRAINING_DIR']} · 썸네일: {app.config['KITTI_THUMBS_DIR']} · "
          f"결과: {app.config['KITTI_RESULTS_DIR']}")
    client = app.test_client()
    sizes: dict[str, list[int]] = {}
    raw = Raw(db.cnf)
    smoke_s1(client, db, raw)
    smoke_s2(client, db, sizes)
    smoke_s5(client)
    smoke_thumbs(client, sizes)
    out = perf(client, args.requests, sizes)
    db.close()
    raw.close()
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n결과: 실패 {len(failures)}건" + ("" if not failures else " — " + "; ".join(failures)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
