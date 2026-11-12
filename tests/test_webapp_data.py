"""웹 앱 실제 데이터 연결 시험 (README F-10, U-4·U-7·U-8, DB 과목 NR-1·NR-4).

실제 MySQL 없이 가짜 DB(FakeDB)를 ``KITTI_DB_FACTORY``로 넣고, 원본 파일은 tmp에 만든다.
실제 DB·원본·결과 파일로 하는 점검은 ``scripts/smoke_webapp.py``가 한다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import struct
from pathlib import Path

import numpy as np
import pytest

from kitti import geometry
from kitti import io as kio
from webapp import create_app
from webapp import db as webdb
from webapp import results as s5
from webapp.i18n import ko

from conftest import CALIB_000000

ROOT = Path(__file__).resolve().parents[1]
REPO_RESULTS = ROOT / "results"
STAMP = dt.datetime(2026, 11, 5, 14, 48, 46)


# ---------------------------------------------------------------------------
# 가짜 DB
# ---------------------------------------------------------------------------
class FakeDB:
    """프로시저 이름 → 결과 집합. 호출 기록을 남긴다. ``fail``을 주면 그 오류를 낸다."""

    def __init__(self, data: dict | None = None, fail: Exception | None = None) -> None:
        self.data = data or {}
        self.fail = fail
        self.calls: list[tuple] = []
        self.closed = False

    def call(self, proc: str, *args):  # noqa: ANN201
        self.calls.append((proc, *args))
        if self.fail is not None:
            raise self.fail
        value = self.data[proc]
        return value(*args) if callable(value) else value

    def close(self) -> None:
        self.closed = True

    def called(self, proc: str) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == proc]


def _obj(object_id, cls, **kw):  # noqa: ANN001, ANN202
    row = {
        "object_id": object_id, "class_name": cls, "is_ignore": 1 if cls == "DontCare" else 0,
        "truncated": 0.0, "occluded": 0, "bbox_left": 100.0, "bbox_top": 150.0,
        "bbox_right": 200.0, "bbox_bottom": 220.0, "dim_h": 1.5, "dim_w": 1.6, "dim_l": 3.9,
        "loc_x": 1.0, "loc_y": 1.6, "loc_z": 20.0, "rotation_y": 0.3, "dist_m": 20.02,
        "diff_level": 1, "valid_3d": 1, "points_in_box": 500, "low_points": 11, "is_low": 0,
    }
    row.update(kw)
    if cls == "DontCare":
        row.update(truncated=None, occluded=None, dim_h=None, dim_w=None, dim_l=None, loc_x=None,
                   loc_y=None, loc_z=None, rotation_y=None, dist_m=None, diff_level=None,
                   valid_3d=None, points_in_box=None, low_points=None, is_low=0)
    return row


SCENE_ROWS = [
    [{"frame_id": 1, "image_w": 1242, "image_h": 375, "n_points": 3, "quality": "ok", "threshold_at": STAMP}],
    [
        _obj(1, "Car"),
        _obj(2, "Pedestrian", points_in_box=5, low_points=19, is_low=1, dist_m=31.2, occluded=1),
        _obj(3, "Car", points_in_box=None, valid_3d=0, dim_h=-1.0, is_low=0),
        _obj(4, "DontCare"),
    ],
]


CLASS_IDS = {"Car": 1, "Van": 2, "Truck": 3, "Pedestrian": 4, "Person_sitting": 5, "Cyclist": 6,
             "Tram": 7, "Misc": 8}


def _ex_row(frame_id, matched=1):  # noqa: ANN001, ANN202
    return {"frame_id": frame_id, "matched_objects": matched, "min_points": 10,
            "nearest_m": 12.34, "farthest_m": 45.67}


def search_data(frames=(311, 7182), total=(37, 39), relax=None):  # noqa: ANN001, ANN201
    relax = relax if relax is not None else [{"cond": "low_only", "scenes": 1278}, {"cond": "dist", "scenes": 315}]
    return {
        "sp_class_ids": lambda names: [[{"class_id": CLASS_IDS[n], "class_name": n} for n in json.loads(names)]],
        "sp_search_summary": [[{"total_scenes": total[0], "total_objects": total[1]}], relax],
        "sp_search_scenes_ex": lambda *a: [[_ex_row(f, 2 if i == 0 else 1) for i, f in enumerate(frames)][: a[9]]],
        "sp_scene_cards": [[{"frame_id": f, "matched_objects": 1, "nearest_m": 12.34, "match_classes": "Pedestrian",
                             "n_objects": 5, "n_low": 1} for f in frames]],
        "sp_scene_detail": SCENE_ROWS,
        "sp_thresholds": [[{"class_id": 1, "class_name": "Car", "low_points": 11, "method": "ntile10_b1",
                            "computed_at": STAMP},
                           {"class_id": 4, "class_name": "Pedestrian", "low_points": 19, "method": "ntile10_b1",
                            "computed_at": STAMP}]],
    }


@pytest.fixture
def training(tmp_path: Path) -> Path:
    """frame 1의 원본 파일: calib, image(PNG 헤더만 맞으면 됨), velodyne(ROI 안 2점 + 밖 2점)."""
    root = tmp_path / "training"
    for sub in ("image_2", "velodyne", "calib", "label_2"):
        (root / sub).mkdir(parents=True)
    (root / "calib" / "000001.txt").write_text(CALIB_000000, encoding="utf-8")
    (root / "image_2" / "000001.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1242, 375) + b"\x08\x02\x00\x00\x00")
    pts = np.array([[5.0, 1.0, -1.0, 0.5], [79.5, -39.5, 0.2, 0.1],
                    [-3.0, 0.0, 0.0, 0.3], [10.0, 45.0, 0.0, 0.3]], dtype=np.float32)
    pts.tofile(root / "velodyne" / "000001.bin")
    return root


@pytest.fixture
def thumbs(tmp_path: Path) -> Path:
    d = tmp_path / "thumbs"
    d.mkdir()
    (d / "000311.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    return d


def make_client(db: FakeDB, training: Path | None = None, thumbs: Path | None = None,
                results: Path | None = None):  # noqa: ANN201
    cfg = {"TESTING": True, "KITTI_DEMO": False, "KITTI_DB_FACTORY": lambda: db}
    if training is not None:
        cfg["KITTI_TRAINING_DIR"] = str(training)
    if thumbs is not None:
        cfg["KITTI_THUMBS_DIR"] = str(thumbs)
    if results is not None:
        cfg["KITTI_RESULTS_DIR"] = str(results)
    return create_app(cfg).test_client()


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
def test_default_is_real_data(monkeypatch):
    monkeypatch.delenv("KITTI_DEMO", raising=False)
    assert create_app({"TESTING": True}).config["KITTI_DEMO"] is False
    monkeypatch.setenv("KITTI_DEMO", "1")
    assert create_app({"TESTING": True}).config["KITTI_DEMO"] is True


def test_db_connection_closed_after_request(training):
    db = FakeDB(search_data())
    make_client(db, training).get("/")
    assert db.closed


# ---------------------------------------------------------------------------
# S1 검색
# ---------------------------------------------------------------------------
def test_s1_maps_conditions_to_procedure_arguments(training, thumbs):
    db = FakeDB(search_data())
    url = "/?cls=Pedestrian&cls=Cyclist&dmin=30&dmax=60&occ=0&occ=3&pmin=5&pmax=50&low_obs=1&sort=far"
    resp = make_client(db, training, thumbs).get(url)
    assert resp.status_code == 200
    assert db.called("sp_class_ids") == [('["Pedestrian", "Cyclist"]',)]
    params = ("[4, 6]", 30, 60, "[0, 3]", 5, 50, 1, 0)
    assert db.called("sp_search_summary") == [params]
    assert db.called("sp_search_scenes_ex") == [params + ("far", 24, 0)]
    assert db.called("sp_scene_cards") == [params + ("[311, 7182]",)]


def test_s1_shows_procedure_rows_in_order_with_counts_from_mysql(training, thumbs):
    db = FakeDB(search_data())
    html = make_client(db, training, thumbs).get("/?cls=Pedestrian&low_obs=1").get_data(as_text=True)
    text = _text(html)
    assert "장면 37 개" in text and "객체 39 개 일치" in text
    assert re.findall(r'class="rcard__link" href="/scenes/(\d+)', html) == ["311", "7182"]
    # 카드 링크는 검색 조건과 결과 안 순번을 담는다(S2 위치·이전·다음)
    assert 'href="/scenes/311?cls=Pedestrian&amp;low_obs=1&amp;i=1"' in html
    assert 'href="/scenes/7182?cls=Pedestrian&amp;low_obs=1&amp;i=2"' in html
    assert 'src="/thumbs/311.webp"' in html and 'loading="lazy"' in html
    assert "12.3 m" in text and "보행자 2" in text
    assert ko.UI["demo_badge"] not in html


def test_s1_far_sort_shows_farthest_distance(training):
    html = make_client(FakeDB(search_data()), training).get("/?sort=far").get_data(as_text=True)
    assert "45.7 m" in _text(html) and "12.3 m" not in _text(html)


def test_s1_occlusion_is_a_set_not_a_range(training):
    db = FakeDB(search_data())
    html = make_client(db, training).get("/?occ=1&occ=3").get_data(as_text=True)
    assert db.called("sp_search_scenes_ex")[0][3] == "[1, 3]"
    assert "가림: 일부 가림 · 알 수 없음" in html
    # 고르지 않은 2는 선택되지 않은 채로 남는다(사이 값을 채우지 않는다)
    assert re.search(r'name="occ" value="2">', html)
    assert re.search(r'name="occ" value="1" checked', html)


def test_s1_multiple_classes_each_have_a_chip(training):
    db = FakeDB(search_data())
    html = make_client(db, training).get("/?cls=Car&cls=Tram").get_data(as_text=True)
    assert "차량 조건 해제" in html and "트램 조건 해제" in html
    # 트램 칩을 빼면 차량만 남는다
    assert re.search(r'href="/\?cls=Car" aria-label="트램 조건 해제"', html)
    assert 'id="cls-car" name="cls" value="Car" checked' in html
    assert 'id="cls-tram" name="cls" value="Tram" checked' in html


def test_s1_distance_and_points_ranges(training):
    db = FakeDB(search_data())
    client = make_client(db, training)
    html = client.get("/?dmin=30&pmax=10").get_data(as_text=True)
    assert "거리 ≥ 30 m" in html and "포인트 ≤ 10" in html
    assert db.called("sp_search_summary")[-1] == (None, 30, None, None, None, 10, 0, 0)
    html = client.get("/?dmin=0&dmax=80").get_data(as_text=True)  # 슬라이더 양 끝 = 제한 없음
    assert db.called("sp_search_summary")[-1] == (None, None, None, None, None, None, 0, 0)
    assert "거리" not in re.search(r'class="chips".*?</div>', html, re.S).group(0)
    html = client.get("/?dmin=10&dmax=40&pmin=5&pmax=100").get_data(as_text=True)
    assert "거리 10–40 m" in html and "포인트 5–100" in html


def test_s1_default_has_no_chips_and_default_occlusion(training):
    db = FakeDB(search_data())
    html = make_client(db, training).get("/").get_data(as_text=True)
    assert db.called("sp_search_summary") == [(None, None, None, None, None, None, 0, 0)]
    assert db.called("sp_search_scenes_ex")[0][8:] == ("match", 24, 0)
    assert db.called("sp_class_ids") == []
    assert "전체 장면 · 조건 없음" in html
    for v in ("0", "1", "2"):
        assert re.search(rf'name="occ" value="{v}" checked', html)


def test_s1_empty_result_suggests_relaxed_condition_from_mysql(training):
    db = FakeDB(search_data(frames=(), total=(0, 0)))
    html = make_client(db, training).get("/?cls=Pedestrian&dmax=20&low_obs=1").get_data(as_text=True)
    assert ko.STATE_MESSAGES["empty_title"] in html
    assert ko.STATE_MESSAGES["empty_relax"].format(field="관측 부족만", count="1,278") in html
    assert db.called("sp_search_scenes_ex") == []  # 0건이면 목록을 부르지 않는다


def test_s1_pagination_offset_and_clamp(training):
    db = FakeDB(search_data(total=(50, 60)))
    client = make_client(db, training)
    client.get("/?page=2")
    assert db.called("sp_search_scenes_ex")[-1][-2:] == (24, 24)
    client.get("/?page=99")  # 50개 → 3쪽이 마지막
    assert db.called("sp_search_scenes_ex")[-1][-2:] == (24, 48)


def test_s1_db_down_shows_nr4_copy_not_traceback(training):
    db = FakeDB(fail=webdb.DBUnavailable("OperationalError: (2003, \"Can't connect to MySQL server\")"))
    resp = make_client(db, training).get("/?cls=Car")
    assert resp.status_code == 503
    html = resp.get_data(as_text=True)
    assert ko.DB_ERROR_MESSAGES["db_unavailable"] in html
    assert ko.STATE_MESSAGES["retry"] in html
    # 운영(debug 아님): 드라이버 메시지(기술 정보)는 화면에 내보내지 않는다(로그에만)
    assert "2003" not in html and "Can&#39;t connect" not in html
    assert "Traceback" not in html
    # 조건 패널은 그대로 남는다(입력 값 보존)
    assert 'id="cls-car" name="cls" value="Car" checked' in html


def test_db_error_details_only_in_debug_and_always_logged(training, caplog):
    fail = webdb.DBUnavailable("OperationalError: (2003, \"Can't connect to MySQL server\")")
    for debug in (False, True):
        app = create_app({"TESTING": True, "DEBUG": debug, "KITTI_DEMO": False,
                          "KITTI_DB_FACTORY": lambda: FakeDB(fail=fail), "KITTI_TRAINING_DIR": str(training)})
        client = app.test_client()
        caplog.clear()
        for url, code in (("/?cls=Car", 503), ("/scenes/1", 503), ("/about", 200)):
            resp = client.get(url)
            assert resp.status_code == code, url
            html = resp.get_data(as_text=True)
            if debug:
                # 기술 정보는 ‘자세히’ 안에만
                details = re.search(r"<details class=\"state__details\">.*?</details>", html, re.S).group(0)
                assert "2003" in details, url
            else:
                assert "2003" not in html and "OperationalError" not in html, url
        assert sum("2003" in r.getMessage() for r in caplog.records) == 3


def test_s1_query_error_uses_query_failed_copy(training):
    db = FakeDB(fail=webdb.DBQueryError("ProgrammingError: (1305, 'PROCEDURE kitti.sp_search_summary does not exist')"))
    resp = make_client(db, training).get("/")
    assert resp.status_code == 503
    assert ko.DB_ERROR_MESSAGES["query_failed"] in resp.get_data(as_text=True)


@pytest.mark.parametrize("sort", ["match", "far", "few_points", "frame"])
def test_s1_all_four_sorts_go_to_the_procedure(training, sort):
    db = FakeDB(search_data())
    html = make_client(db, training).get(f"/?sort={sort}").get_data(as_text=True)
    assert db.called("sp_search_scenes_ex")[0][8] == sort
    assert re.search(rf'<option value="{sort}" selected', html)
    assert "disabled" not in re.search(r'<select id="sort-select".*?</select>', html, re.S).group(0)


def test_s1_unknown_sort_falls_back_to_default(training):
    db = FakeDB(search_data())
    make_client(db, training).get("/?sort=evil'--")
    assert db.called("sp_search_scenes_ex")[0][8] == "match"


# ---------------------------------------------------------------------------
# S2 장면 상세
# ---------------------------------------------------------------------------
def test_s2_objects_from_mysql_with_image_and_footprints(training):
    db = FakeDB(search_data())
    resp = make_client(db, training).get("/scenes/1")
    assert resp.status_code == 200
    assert db.called("sp_scene_detail") == [(1,)]
    html = resp.get_data(as_text=True)
    assert 'src="/scenes/1/image.png"' in html
    rows = re.findall(r'<tr data-obj="(\d+)"', html)
    assert rows == ["1", "2", "3"]  # DontCare는 표에 넣지 않는다
    assert ko.UI["points_null"] in html  # NULL ≠ 0
    assert "관측 부족" in html and "⚠" in html
    assert "무시 영역 (1)" in html
    data = json.loads(re.search(r'id="scene-data">(.*?)</script>', html, re.S).group(1))
    assert data["footprint_ids"] == [1, 2]  # 3D 박스 무효(3)·DontCare(4)는 BEV 박스 없음
    assert len(data["footprints"][0]) == 4
    assert data["roi"] == {"fwd_min": 0.0, "fwd_max": 80.0, "left_min": -40.0, "left_max": 40.0}
    assert "2026-11-05 14:48" in html


def test_s2_footprint_matches_calib_transform(training):
    db = FakeDB(search_data())
    html = make_client(db, training).get("/scenes/1").get_data(as_text=True)
    data = json.loads(re.search(r'id="scene-data">(.*?)</script>', html, re.S).group(1))
    calib = kio.parse_calib(training / "calib" / "000001.txt")
    o = SCENE_ROWS[1][0]
    rect = geometry.box_footprint_rect(o["dim_l"], o["dim_w"], o["loc_x"], o["loc_y"], o["loc_z"], o["rotation_y"])
    velo = geometry.rect_to_velo(rect, calib)
    np.testing.assert_allclose(np.array(data["footprints"][0]), velo[:, :2], atol=1e-3)
    # 카메라 전방 20 m → LiDAR 전방 약 20 m
    assert 18 < np.mean(velo[:, 0]) < 22


def test_s2_position_prev_next_from_procedure(training):
    def scenes(*a):  # noqa: ANN001, ANN202
        return [[_ex_row(f) for f in (9, 1, 42)][: a[9]]]

    data = search_data()
    data["sp_search_scenes_ex"] = scenes
    db = FakeDB(data)
    html = make_client(db, training).get("/scenes/1?cls=Pedestrian&i=2").get_data(as_text=True)
    assert db.called("sp_search_scenes_ex") == [("[4]", None, None, None, None, None, 0, 0, "match", 3, 0)]
    assert "(2 / 37)" in _text(html)
    assert 'href="/scenes/9?cls=Pedestrian&amp;i=1"' in html
    assert 'href="/scenes/42?cls=Pedestrian&amp;i=3"' in html
    assert 'href="/?cls=Pedestrian" data-nav-back' in html


def test_s2_stale_position_is_hidden(training):
    data = search_data()
    data["sp_search_scenes_ex"] = [[_ex_row(5)]]
    html = make_client(FakeDB(data), training).get("/scenes/1?i=1").get_data(as_text=True)
    assert "data-nav-prev" not in html and " / " not in _text(re.search(r"scene-bar__back.*?</a>", html, re.S).group(0))


def test_s2_partial_states_when_files_missing(tmp_path):
    empty = tmp_path / "training"
    empty.mkdir()
    client = make_client(FakeDB(search_data()), empty)
    html = client.get("/scenes/1").get_data(as_text=True)
    assert ko.STATE_MESSAGES["partial_image"].format(filename="000001.png") in html
    assert ko.STATE_MESSAGES["partial_calib"].format(filename="000001.txt") in html
    # 표는 그대로 보여준다(부분 성공)
    assert re.findall(r'<tr data-obj="(\d+)"', html) == ["1", "2", "3"]
    assert client.get("/scenes/1/points.bin").status_code == 404
    assert client.get("/scenes/1/image.png").status_code == 404


def test_s2_unknown_and_out_of_range_frames(training):
    data = search_data()
    data["sp_scene_detail"] = [[], []]
    db = FakeDB(data)
    client = make_client(db, training)
    assert client.get("/scenes/42").status_code == 404
    assert client.get("/scenes/7481").status_code == 404
    assert client.get("/scenes/-1").status_code == 404
    assert db.called("sp_scene_detail") == [(42,)]


def test_s2_db_down_is_designed_error(training):
    resp = make_client(FakeDB(fail=webdb.DBUnavailable("OperationalError 2003")), training).get("/scenes/1")
    assert resp.status_code == 503
    html = resp.get_data(as_text=True)
    assert ko.DB_ERROR_MESSAGES["db_unavailable"] in html and "OperationalError 2003" not in html


def test_points_endpoint_reads_real_bin_and_clips_roi(training):
    resp = make_client(FakeDB(), training).get("/scenes/1/points.bin")
    assert resp.status_code == 200 and resp.mimetype == "application/octet-stream"
    assert resp.headers["X-Point-Stride"] == "16" and "X-Demo-Data" not in resp.headers
    pts = np.frombuffer(resp.data, dtype="<f4").reshape(-1, 4)
    np.testing.assert_array_equal(pts, np.array([[5.0, 1.0, -1.0, 0.5], [79.5, -39.5, 0.2, 0.1]], dtype=np.float32))


def test_image_route(training):
    resp = make_client(FakeDB(), training).get("/scenes/1/image.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert "max-age" in resp.headers["Cache-Control"]


def test_thumbnail_route_is_safe_and_cached(training, thumbs):
    client = make_client(FakeDB(), training, thumbs)
    resp = client.get("/thumbs/311.webp")
    assert resp.status_code == 200 and resp.mimetype == "image/webp"
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert client.get("/thumbs/312.webp").status_code == 404      # 파일 없음
    assert client.get("/thumbs/7481.webp").status_code == 404     # 범위 밖
    assert client.get("/thumbs/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/thumbs/abc.webp").status_code == 404


# ---------------------------------------------------------------------------
# S6 기준값
# ---------------------------------------------------------------------------
def test_s6_thresholds_from_mysql(training):
    html = make_client(FakeDB(search_data()), training).get("/about").get_data(as_text=True)
    assert "산출 일시: 2026-11-05 14:48" in html
    assert re.search(r"보행자</span></td><td class=\"is-num\">19</td>", html)


def test_s6_db_down_keeps_page(training):
    resp = make_client(FakeDB(fail=webdb.DBUnavailable("down")), training).get("/about")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert ko.STATE_MESSAGES["thresholds_error"] in html and "√(x² + z²)" in html


# ---------------------------------------------------------------------------
# S5 처리 결과
# ---------------------------------------------------------------------------
def test_s5_conclusions_equal_result_files():
    """U-7: 한 줄 결론의 숫자가 결과 파일 값과 같다(저장소의 results/)."""
    client = make_client(FakeDB())
    html = client.get("/pipeline").get_data(as_text=True)
    text = _text(html)
    q = json.loads((REPO_RESULTS / "quality_report.json").read_text(encoding="utf-8"))
    a = json.loads((REPO_RESULTS / "analysis_summary.json").read_text(encoding="utf-8"))
    comp = q["completeness"]
    assert (f"{comp['n_expected']:,}개 장면 중 {comp['n_linked']:,}개 연결, 일반 객체 좌표 오류 "
            f"{q['labels']['object_errors']['n_objects_with_error']:,}건") in text
    assert f"보행자의 {a['f7']['share_within_20m_pct']['Pedestrian']:.1f}%가 20 m 이내" in text
    assert f"객체 {a['f9']['n_low_objects']:,}개, 장면 {a['f9']['n_low_scenes']:,}개" in text
    assert f"{a['f6']['share_points_x_lt_20m_pct']:.1f}%" in text
    m = json.loads((REPO_RESULTS / "mysql_consistency.json").read_text(encoding="utf-8"))
    assert f"{m['summary']['n_ok']} / {m['summary']['n_checks']}" in text
    v = json.loads((REPO_RESULTS / "verification.json").read_text(encoding="utf-8"))
    assert f"{v['n_items']}개 항목 모두 일치" in text
    # 13개 항목 × 3개 규모
    table = re.search(r'id="fig-accuracy".*?</table>', html, re.S).group(0)
    assert len(re.findall(r"<tr><td>", table)) == v["n_items"]
    assert table.count("통과") == v["n_items"] * 3
    # 그림은 결과 파일 경로로 연결
    assert 'src="/pipeline/files/figures/f6_grid_cumulative.png"' in html
    assert ko.UI["demo_badge"] not in html


def test_s5_bench_missing_shows_pending_not_numbers(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(REPO_RESULTS, results, ignore=shutil.ignore_patterns("bench"))
    html = make_client(FakeDB(), results=results).get("/pipeline").get_data(as_text=True)
    assert ko.STATE_MESSAGES["bench_pending_title"] in html
    assert 'id="chart-speedup"' not in html
    assert "성능은 측정 전입니다" in html


def test_s5_with_bench_summary(tmp_path):
    results = tmp_path / "results"
    shutil.copytree(REPO_RESULTS, results, ignore=shutil.ignore_patterns("bench"))
    bench = results / "bench"
    bench.mkdir()
    header = ("mode,scale,cores,n_runs,n_exit0,n_verify_pass,wall_s_median,wall_s_min,wall_s_max,"
              "peak_rss_mib_median,peak_rss_mib_min,peak_rss_mib_max,speedup_vs_seq_median,"
              "speedup_vs_seq_min,speedup_vs_seq_max\n")
    rows = ["seq,7481,1,3,3,3,100,99,101,500,490,510,1,0.98,1.02",
            "local8,7481,8,3,3,3,25,24,26,4000,3900,4100,4,3.85,4.17"]
    (bench / "summary.csv").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    (bench / "environment.json").write_text(json.dumps({"cpu_model": "Test CPU", "os": "TestOS"}), encoding="utf-8")
    html = make_client(FakeDB(), results=results).get("/pipeline").get_data(as_text=True)
    assert "7,481개 장면에서 local[8]이 순차 대비 4.00배" in html
    assert 'id="chart-speedup"' in html and "Test CPU" in html
    data = json.loads(re.search(r'id="pipeline-data">(.*?)</script>', html, re.S).group(1))
    assert data["speedup"] == [{"cores": 8, "median": 4.0, "lo": 3.85, "hi": 4.17}]


def test_s5_empty_results_dir_shows_missing_states(tmp_path):
    resp = make_client(FakeDB(), results=tmp_path).get("/pipeline")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count(ko.STATE_MESSAGES["results_missing"]) >= 4
    assert "/pipeline/files/" not in html  # 없는 파일은 링크하지 않는다


def test_result_file_route_is_restricted():
    client = make_client(FakeDB())
    assert client.get("/pipeline/files/verification.json").status_code == 200
    assert client.get("/pipeline/files/subsets/sample_100/verification.json").status_code == 200
    assert client.get("/pipeline/files/../README.md").status_code == 404
    assert client.get("/pipeline/files/%2E%2E/README.md").status_code == 404
    assert client.get("/pipeline/files/units.txt").status_code == 404
    assert client.get("/pipeline/files/nope.json").status_code == 404
    assert s5.servable(REPO_RESULTS, "../README.md") is False


# ---------------------------------------------------------------------------
# DB 계층
# ---------------------------------------------------------------------------
def test_mysql_missing_option_file_is_unavailable(tmp_path):
    db = webdb.MySQLDatabase(tmp_path / "no.cnf")
    with pytest.raises(webdb.DBUnavailable, match="option file not found"):
        db.call("sp_thresholds")


def test_mysql_refused_connection_is_unavailable(tmp_path):
    cnf = tmp_path / "my.cnf"
    cnf.write_text("[client]\nuser=nobody\npassword=x\nhost=127.0.0.1\nport=1\ndatabase=kitti\n", encoding="utf-8")
    db = webdb.MySQLDatabase(cnf, connect_timeout=1)
    with pytest.raises(webdb.DBUnavailable):
        db.call("sp_thresholds")


def test_cnf_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KITTI_MYSQL_CNF", str(tmp_path / "x.cnf"))
    assert webdb.cnf_path() == tmp_path / "x.cnf"
    monkeypatch.delenv("KITTI_MYSQL_CNF")
    assert webdb.cnf_path() == Path("~/.my.cnf").expanduser()


def test_only_fixed_procedure_calls_no_sql_assembly():
    """NR-1: 웹 앱 Python에는 고정된 CALL 문만 있다(SELECT·INSERT 등 SQL 문자열 없음)."""
    for py in (ROOT / "webapp").rglob("*.py"):
        code = py.read_text(encoding="utf-8")
        assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\s", code), py.name
    for sql in webdb.PROCEDURES.values():
        assert re.fullmatch(r"CALL sp_\w+\((%s(, %s)*)?\)", sql), sql
    procs = (ROOT / "db" / "03_views_procs.sql").read_text(encoding="utf-8")
    for name in webdb.PROCEDURES:
        assert f"CREATE PROCEDURE {name}(" in procs, name


# ---------------------------------------------------------------------------
# 좌표 변환 (BEV 박스)
# ---------------------------------------------------------------------------
def test_rect_to_velo_inverts_velo_to_rect(calib_file):
    calib = kio.parse_calib(calib_file)
    pts = np.array([[10.0, 2.0, -1.0], [35.5, -12.0, 0.4], [70.0, 30.0, 2.0]])
    np.testing.assert_allclose(geometry.rect_to_velo(geometry.velo_to_rect(pts, calib), calib), pts, atol=1e-9)


def test_footprint_corners_are_on_box_boundary():
    l, w, x, y, z, ry = 3.9, 1.6, 1.0, 1.6, 20.0, 0.7
    corners = geometry.box_footprint_rect(l, w, x, y, z, ry)
    inside = geometry.points_in_box_mask(corners, 1.5, w, l, x, y, z, ry)
    assert inside.all()
    pushed_out = corners + (corners - np.array([x, y, z])) * 0.05  # 중심에서 5% 바깥
    assert not geometry.points_in_box_mask(pushed_out, 1.5, w, l, x, y, z, ry).any()


# ---------------------------------------------------------------------------
# 확장 검색 프로시저 정적 점검 (db/03_views_procs.sql)
# ---------------------------------------------------------------------------
PROCS_SQL = (ROOT / "db" / "03_views_procs.sql").read_text(encoding="utf-8")


def _proc_body(name: str) -> str:
    m = re.search(rf"CREATE PROCEDURE {name}\((.*?)\)\s*BEGIN(.*?)END //", PROCS_SQL, re.S)
    assert m, name
    return m.group(1), m.group(2)


def _norm(s: str) -> str:
    return " ".join(s.split())


FILTER_ARGS = ["IN p_classes JSON", "IN p_min_dist DOUBLE", "IN p_max_dist DOUBLE", "IN p_occ JSON",
               "IN p_min_points INT", "IN p_max_points INT", "IN p_low_only BOOLEAN", "IN p_unreviewed BOOLEAN"]

#: 세 프로시저가 똑같이 써야 하는 조건식 (검색 의미가 같아야 결과 수·카드·목록이 맞는다)
FILTER_ATOMS = [
    "p_classes IS NULL OR JSON_LENGTH(p_classes) = 0 OR v.class_id MEMBER OF (p_classes)",
    "p_min_dist IS NULL OR v.dist_m >= p_min_dist",
    "p_max_dist IS NULL OR v.dist_m <= p_max_dist",
    "CASE WHEN p_occ IS NULL OR JSON_LENGTH(p_occ) = 0 THEN v.occluded BETWEEN 0 AND 2 "
    "ELSE v.occluded MEMBER OF (p_occ) END",
    "p_min_points IS NULL OR v.points_in_box >= p_min_points",
    "p_max_points IS NULL OR v.points_in_box <= p_max_points",
    "NOT COALESCE(p_unreviewed, FALSE) OR r.frame_id IS NULL",
]


def _params(sig: str) -> list[str]:
    return [_norm(p) for p in sig.split(",")]


def test_ex_procedure_signatures():
    assert _params(_proc_body("sp_search_scenes_ex")[0]) == FILTER_ARGS + [
        "IN p_sort VARCHAR(16)", "IN p_limit INT", "IN p_offset INT"]
    assert _params(_proc_body("sp_search_summary")[0]) == FILTER_ARGS
    assert _params(_proc_body("sp_scene_cards")[0]) == FILTER_ARGS + ["IN p_frames JSON"]


def test_ex_filter_semantics_shared_by_list_summary_and_cards():
    for name in ("sp_search_scenes_ex", "sp_search_summary", "sp_scene_cards"):
        body = _norm(_proc_body(name)[1])
        for atom in FILTER_ATOMS:
            assert atom in body, (name, atom)
        assert "v_object_obs v" in body and "v_object_review_summary r" in body, name
    # 관측 부족 조건: 목록·카드는 sp_search_scenes와 같은 식, 요약은 NULL을 FALSE로(같은 효과)
    for name in ("sp_search_scenes_ex", "sp_scene_cards"):
        assert "NOT COALESCE(p_low_only, FALSE) OR v.is_low" in _norm(_proc_body(name)[1])
    assert "NOT COALESCE(p_low_only, FALSE) OR COALESCE(v.is_low, FALSE)" in _norm(_proc_body("sp_search_summary")[1])


def test_ex_sort_keys_match_screen_options_and_base_order():
    body = _norm(_proc_body("sp_search_scenes_ex")[1])
    keys = re.search(r"NOT IN \(([^)]*)\)", body).group(1)
    assert [k.strip(" '") for k in keys.split(",")] == list(ko.SORT_OPTIONS)
    # 기본 정렬(match)은 sp_search_scenes와 같은 ORDER BY matched_objects DESC, frame_id
    assert "CASE WHEN COALESCE(p_sort, 'match') = 'match' THEN COUNT(*) END DESC" in body
    assert body.rstrip(" ;").endswith("v.frame_id LIMIT p_limit OFFSET p_offset")
    base = _norm(_proc_body("sp_search_scenes")[1])
    assert "ORDER BY matched_objects DESC, v.frame_id" in base
    # 앞 세 열은 sp_search_scenes와 같은 정의
    assert "SELECT v.frame_id, COUNT(*) AS matched_objects, MIN(v.points_in_box) AS min_points" in base
    assert "SELECT v.frame_id, COUNT(*) AS matched_objects, MIN(v.points_in_box) AS min_points" in body


def test_base_procedure_untouched():
    sig, body = _proc_body("sp_search_scenes")
    assert _params(sig) == ["IN p_class TINYINT", "IN p_max_dist DOUBLE", "IN p_occ_min TINYINT",
                            "IN p_occ_max TINYINT", "IN p_max_points INT", "IN p_low_only BOOLEAN",
                            "IN p_unreviewed BOOLEAN", "IN p_limit INT", "IN p_offset INT"]
    assert "MEMBER OF" not in body and "p_sort" not in body
