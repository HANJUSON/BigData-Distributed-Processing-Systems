"""kitti.geometry 시험: 좌표 변환, 박스 포함(회전·ε 경계), 난이도, 거리, 격자, 범위 검사."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from kitti import config
from kitti import geometry as g
from kitti import io as kio

EPS = config.EPS


def _obj(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "class": "Car", "truncated": 0.0, "occluded": 0, "alpha": 0.0,
        "bbox_left": 100.0, "bbox_top": 100.0, "bbox_right": 200.0, "bbox_bottom": 160.0,
        "h": 1.5, "w": 2.0, "l": 4.0, "x": 1.0, "y": 1.7, "z": 10.0, "ry": 0.0,
    }
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# 좌표 변환
# --------------------------------------------------------------------------
def test_velo_to_rect_matches_homogeneous(calib_file: Path) -> None:
    calib = kio.parse_calib(calib_file)
    pts = np.array([[10.0, 1.0, -1.0], [25.5, -4.0, 0.3], [0.0, 0.0, 0.0]], dtype=np.float32)
    tr = np.vstack([calib["Tr_velo_to_cam"], [0, 0, 0, 1]])
    r0 = np.eye(4)
    r0[:3, :3] = calib["R0_rect"]
    homo = np.hstack([pts.astype(np.float64), np.ones((3, 1))])
    expected = (r0 @ tr @ homo.T).T[:, :3]
    got = g.velo_to_rect(pts, calib)
    assert got.dtype == np.float64
    np.testing.assert_allclose(got, expected, rtol=0, atol=1e-12)
    # 전방 10 m 점은 카메라 z ≈ 10 m 근처
    assert 9.0 < got[0, 2] < 11.0


# --------------------------------------------------------------------------
# 유효성·거리·구간
# --------------------------------------------------------------------------
def test_is_valid_3d() -> None:
    assert g.is_valid_3d("Car", 1.5, 1.6, 3.9, 10.0)
    assert not g.is_valid_3d("DontCare", 1.5, 1.6, 3.9, 10.0)
    assert not g.is_valid_3d("Car", 0.0, 1.6, 3.9, 10.0)
    assert not g.is_valid_3d("Car", 1.5, -1.0, 3.9, 10.0)
    assert not g.is_valid_3d("Car", 1.5, 1.6, 0.0, 10.0)
    assert not g.is_valid_3d("Car", 1.5, 1.6, 3.9, 0.0)
    assert not g.is_valid_3d("Car", 1.5, 1.6, 3.9, -2.0)
    assert not g.is_valid_3d("Car", float("nan"), 1.6, 3.9, 5.0)


def test_distance_m() -> None:
    assert g.distance_m(3.0, 4.0) == 5.0
    assert g.distance_m(-16.53, 58.49) == math.sqrt(16.53 * 16.53 + 58.49 * 58.49)
    np.testing.assert_array_equal(g.distance_m([3.0, 0.0], [4.0, 2.0]), [5.0, 2.0])


def test_dist_bin_matches_db_function() -> None:
    assert g.dist_bin(0.0) == "0-10"
    assert g.dist_bin(9.999) == "0-10"
    assert g.dist_bin(10.0) == "10-20"
    assert g.dist_bin(19.99) == "10-20"
    assert g.dist_bin(20.0) == "20-40"
    assert g.dist_bin(40.0) == "40+"
    assert g.dist_bin(150.0) == "40+"
    assert g.dist_bin(float("nan")) is None
    assert g.dist_bin(None) is None
    assert list(g.dist_bin([5.0, 15.0, 39.9, 41.0])) == ["0-10", "10-20", "20-40", "40+"]


# --------------------------------------------------------------------------
# 난이도
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("bh", "occ", "trunc", "expected"),
    [
        (40.0, 0, 0.15, (True, True, True, 1)),  # Easy 경계값 포함
        (39.99, 0, 0.0, (False, True, True, 2)),  # 높이 부족 → Moderate
        (40.0, 1, 0.0, (False, True, True, 2)),  # occluded 1 → Moderate
        (40.0, 0, 0.16, (False, True, True, 2)),  # truncated 0.16 → Moderate
        (25.0, 1, 0.30, (False, True, True, 2)),  # Moderate 경계값 포함
        (24.99, 0, 0.0, (False, False, False, 4)),  # 높이 25 미만 → 없음
        (25.0, 2, 0.30, (False, False, True, 3)),  # occluded 2 → Hard
        (25.0, 1, 0.31, (False, False, True, 3)),  # truncated 0.31 → Hard
        (30.0, 2, 0.50, (False, False, True, 3)),  # Hard 경계값 포함
        (30.0, 3, 0.0, (False, False, False, 4)),  # occluded 3 → 없음
        (30.0, 0, 0.51, (False, False, False, 4)),  # truncated 0.51 → 없음
    ],
)
def test_difficulty_thresholds(bh: float, occ: int, trunc: float, expected: tuple) -> None:
    d = g.difficulty(bh, occ, trunc)
    assert (d["eligible_easy"], d["eligible_moderate"], d["eligible_hard"], d["diff_level"]) == expected
    assert d["eligible_moderate"] == (d["diff_level"] <= 2)


def test_difficulty_from_text_values() -> None:
    # 라벨 문자열에서 float64로 파싱한 값이 경계값과 정확히 같아야 한다.
    rec = kio.parse_label_line("Car 0.15 0 0 10.00 100.00 50.00 140.00 1 1 1 0 0 5 0", 1)
    bh = g.bbox_height(rec["bbox_top"], rec["bbox_bottom"])
    assert bh == 40.0
    assert g.difficulty(bh, rec["occluded"], rec["truncated"])["diff_level"] == 1
    rec = kio.parse_label_line("Car 0.30 1 0 10.00 100.00 50.00 125.00 1 1 1 0 0 5 0", 2)
    bh = g.bbox_height(rec["bbox_top"], rec["bbox_bottom"])
    assert bh == 25.0 and rec["truncated"] == 0.30
    assert g.difficulty(bh, rec["occluded"], rec["truncated"])["diff_level"] == 2
    rec = kio.parse_label_line("Car 0.50 2 0 10.00 100.00 50.00 125.00 1 1 1 0 0 5 0", 3)
    assert g.difficulty(25.0, rec["occluded"], rec["truncated"])["diff_level"] == 3

def test_difficulty_dontcare_never_eligible() -> None:
    d = g.difficulty(100.0, -1, -1.0, is_dontcare=True)
    assert d == {"eligible_easy": False, "eligible_moderate": False, "eligible_hard": False, "diff_level": 4}


def test_difficulty_vectorised() -> None:
    d = g.difficulty(np.array([50.0, 30.0, 30.0, 50.0]), np.array([0, 1, 2, -1]),
                     np.array([0.0, 0.2, 0.4, -1.0]), np.array([False, False, False, True]))
    np.testing.assert_array_equal(d["diff_level"], [1, 2, 3, 4])
    np.testing.assert_array_equal(d["eligible_moderate"], d["diff_level"] <= 2)


# --------------------------------------------------------------------------
# 3D 박스 포함
# --------------------------------------------------------------------------
def _box_points(obj: dict[str, Any], local: np.ndarray) -> np.ndarray:
    """박스 지역 좌표(lx, ly, lz)를 rect 좌표로 되돌린다(판정식의 역변환)."""
    c, s = math.cos(obj["ry"]), math.sin(obj["ry"])
    lx, ly, lz = local[:, 0], local[:, 1], local[:, 2]
    dx = c * lx + s * lz
    dz = -s * lx + c * lz
    return np.stack([dx + obj["x"], ly + obj["y"], dz + obj["z"]], axis=1)


@pytest.mark.parametrize("ry", [0.0, math.pi / 2, math.pi / 4, -1.2])
def test_box_boundaries_with_eps(ry: float) -> None:
    obj = _obj(ry=ry)
    half_l, half_w, h = obj["l"] / 2, obj["w"] / 2, obj["h"]
    inside = np.array([
        [0.0, -h / 2, 0.0],  # 중심
        [half_l, -0.1, 0.0],  # l/2 경계 정확히
        [-half_l - EPS / 2, -0.1, 0.0],  # l/2 + ε/2
        [0.0, -0.1, half_w + EPS / 2],  # w/2 + ε/2
        [0.0, 0.0, 0.0],  # 바닥면(ly = 0)
        [0.0, EPS / 2, 0.0],  # 바닥 아래 ε/2
        [0.0, -h - EPS / 2, 0.0],  # 윗면 위 ε/2
        [half_l, -h, -half_w],  # 꼭짓점
    ])
    outside = np.array([
        [half_l + 2 * EPS, -0.1, 0.0],
        [-half_l - 2 * EPS, -0.1, 0.0],
        [0.0, -0.1, half_w + 2 * EPS],
        [0.0, -0.1, -half_w - 2 * EPS],
        [0.0, 2 * EPS, 0.0],
        [0.0, -h - 2 * EPS, 0.0],
        [half_l + 1.0, -0.1, 0.0],
    ])
    pts_in = _box_points(obj, inside)
    pts_out = _box_points(obj, outside)
    mask_in = g.points_in_box_mask(pts_in, obj["h"], obj["w"], obj["l"], obj["x"], obj["y"], obj["z"], obj["ry"])
    mask_out = g.points_in_box_mask(pts_out, obj["h"], obj["w"], obj["l"], obj["x"], obj["y"], obj["z"], obj["ry"])
    assert mask_in.all(), mask_in
    assert not mask_out.any(), mask_out
    counts = g.points_in_box_counts(np.vstack([pts_in, pts_out]), [obj])
    assert counts == [len(inside)]


def test_rotation_pi_over_2_swaps_axes() -> None:
    # ry = π/2: 박스 길이(l=4) 방향이 카메라 z축이 된다.
    obj = _obj(ry=math.pi / 2, x=0.0, y=0.0, z=10.0)
    pts = np.array([
        [0.0, -0.5, 10.0 + 1.9],  # z 방향 1.9 m: 길이 안
        [1.9, -0.5, 10.0],  # x 방향 1.9 m: 너비(1.0) 밖
        [0.9, -0.5, 10.0],  # x 방향 0.9 m: 너비 안
    ])
    mask = g.points_in_box_mask(pts, obj["h"], obj["w"], obj["l"], obj["x"], obj["y"], obj["z"], obj["ry"])
    assert mask.tolist() == [True, False, True]


def test_rotation_pi_over_4_diagonal() -> None:
    obj = _obj(ry=math.pi / 4, x=0.0, y=0.0, z=10.0, l=4.0, w=2.0)
    r = 1.9 / math.sqrt(2)
    # ry=π/4이면 길이축은 rect (x, z) 방향 (cos, -sin) = (1/√2, -1/√2)
    pts = np.array([
        [r, -0.5, 10.0 - r],  # 길이축 1.9 m → 안
        [r, -0.5, 10.0 + r],  # 너비축 1.9 m → 밖 (w/2 = 1)
        [2.2, -0.5, 10.0],  # 축 정렬 기준으로는 안이지만 회전 박스에서는 밖
    ])
    mask = g.points_in_box_mask(pts, obj["h"], obj["w"], obj["l"], obj["x"], obj["y"], obj["z"], obj["ry"])
    assert mask.tolist() == [True, False, False]


def test_points_in_box_counts_invalid_is_none() -> None:
    pts = np.array([[1.0, 1.0, 10.0], [100.0, 0.0, 100.0]])
    objs = [
        _obj(),
        _obj(**{"class": "DontCare", "h": -1.0, "w": -1.0, "l": -1.0, "x": -1000.0, "y": -1000.0, "z": -1000.0, "ry": -10.0}),
        _obj(h=0.0),
        _obj(z=-1.0),
        _obj(x=50.0),  # 유효하지만 포인트 없음 → 0
    ]
    assert g.points_in_box_counts(pts, objs) == [1, None, None, None, 0]
    assert g.points_in_box_counts(np.empty((0, 3)), [_obj()]) == [0]


def test_box_count_on_real_calib_projection(calib_file: Path) -> None:
    # Velodyne 점을 rect로 변환한 뒤 그 점 주위에 박스를 놓으면 포함되어야 한다.
    calib = kio.parse_calib(calib_file)
    velo = np.array([[20.0, 2.0, -0.8]], dtype=np.float32)
    rect = g.velo_to_rect(velo, calib)
    x, y, z = rect[0]
    obj = _obj(x=x, y=y + 0.5, z=z, h=1.0, w=1.0, l=1.0, ry=0.3)
    assert g.points_in_box_counts(rect, [obj]) == [1]


# --------------------------------------------------------------------------
# 격자
# --------------------------------------------------------------------------
def test_grid_edges_and_counts() -> None:
    pts = np.array([
        [0.0, -40.0, 1.0, 0.0],  # (0, 0) 포함
        [0.5, -39.5, 3.0, 0.0],  # (0, 0)
        [79.999, 39.999, 2.0, 0.0],  # (79, 79)
        [80.0, 0.0, 0.0, 0.0],  # x=80 제외
        [10.0, 40.0, 0.0, 0.0],  # y=40 제외
        [-0.001, 0.0, 0.0, 0.0],  # x<0 제외
        [10.0, -40.001, 0.0, 0.0],  # y<-40 제외
        [10.2, -0.5, -1.0, 0.0],  # 음수 y: floor(39.5) = 39
        [10.9, -0.0001, 5.0, 0.0],  # (10, 39)
        [3.0, 0.0, 0.0, 0.0],  # y=0 → grid_y 40
        [np.nan, 0.0, 0.0, 0.0],  # NaN 제외
        [5.0, np.inf, 0.0, 0.0],  # Inf 제외
    ], dtype=np.float32)
    res = g.grid_cell_counts(pts)
    cells = list(zip(res["grid_x"].tolist(), res["grid_y"].tolist(), res["n_points"].tolist()))
    assert cells == [(0, 0, 2), (3, 40, 1), (10, 39, 2), (79, 79, 1)]
    assert res["grid_x"].dtype == np.int32 and res["n_points"].dtype == np.int64
    assert res["z_sum"].tolist() == [4.0, 0.0, 4.0, 2.0]
    assert res["z_min"].tolist() == [1.0, 0.0, -1.0, 2.0]
    assert res["z_max"].tolist() == [3.0, 0.0, 5.0, 2.0]
    assert int(res["n_points"].sum()) == 6


def test_grid_uses_float64_of_float32() -> None:
    # float32(79.99999) == 80.0 이므로 제외되어야 한다(float32 값 그대로 float64 변환).
    x32 = np.float32(79.999999)
    assert float(x32) == 80.0
    res = g.grid_cell_counts(np.array([[x32, 0.0, 0.0, 0.0]], dtype=np.float32))
    assert res["n_points"].size == 0
    # float32(0.1)은 float64로 0.10000000149…: floor는 0
    res = g.grid_cell_counts(np.array([[0.1, -39.9, 0.0, 0.0]], dtype=np.float32))
    assert (int(res["grid_x"][0]), int(res["grid_y"][0])) == (0, 0)


def test_grid_empty() -> None:
    res = g.grid_cell_counts(np.empty((0, 4), dtype=np.float32))
    assert all(v.size == 0 for v in res.values())


# --------------------------------------------------------------------------
# F-2 범위 검사
# --------------------------------------------------------------------------
def test_object_range_errors() -> None:
    assert g.object_range_errors(_obj(), 1242, 375) == []
    dc = _obj(**{"class": "DontCare", "truncated": -1.0, "occluded": -1, "alpha": -10.0, "ry": -10.0})
    assert g.object_range_errors(dc, 1242, 375) == []
    assert g.object_range_errors(_obj(truncated=1.2)) == ["truncated_range"]
    assert g.object_range_errors(_obj(occluded=4)) == ["occluded_range"]
    assert g.object_range_errors(_obj(alpha=3.2, ry=-3.2)) == ["alpha_range", "ry_range"]
    assert g.object_range_errors(_obj(bbox_left=300.0)) == ["bbox_order"]
    assert g.object_range_errors(_obj(bbox_right=1300.0), 1242, 375) == ["bbox_outside_image"]
    assert g.object_range_errors(_obj(bbox_right=1300.0)) == []  # 이미지 크기 모름
    assert "non_finite" in g.object_range_errors(_obj(x=float("nan")))


def test_point_range_masks() -> None:
    pts = np.array([
        [1.0, 2.0, 3.0, 0.5],
        [np.nan, 0.0, 0.0, 0.5],
        [201.0, 0.0, 0.0, 0.5],
        [0.0, 0.0, -200.0, 1.0],
        [0.0, 0.0, 0.0, 1.5],
        [0.0, 0.0, 0.0, -0.1],
    ], dtype=np.float32)
    m = g.point_range_masks(pts)
    assert m["non_finite"].tolist() == [False, True, False, False, False, False]
    assert m["coord_range"].tolist() == [False, False, True, False, False, False]
    assert m["reflectance_range"].tolist() == [False, False, False, False, True, True]
    assert m["any"].tolist() == [False, True, True, False, True, True]


def test_grid_cell_counts_drops_non_finite_z() -> None:
    """L1: z가 NaN/Inf인 점은 격자에서 빠지고 같은 격자의 z 통계를 오염시키지 않는다."""
    pts = np.array(
        [[1.5, 0.5, np.nan, 0.1], [1.5, 0.5, np.inf, 0.1], [1.5, 0.5, -np.inf, 0.1],
         [1.5, 0.5, 0.25, 0.1], [1.5, 0.5, -0.75, 0.1]],
        dtype=np.float32,
    )
    out = g.grid_cell_counts(pts)
    assert out["grid_x"].tolist() == [1] and out["grid_y"].tolist() == [40]
    assert out["n_points"].tolist() == [2]
    assert out["z_sum"].tolist() == [-0.5] and out["z_min"].tolist() == [-0.75] and out["z_max"].tolist() == [0.25]
    mask, _, _ = g.grid_index(pts)
    assert mask.tolist() == [False, False, False, True, True]
