"""좌표 변환·3D 박스 포함 판정·거리·난이도·격자 집계 (numpy float64).

Spark(mapPartitions)와 순차 처리(for 루프)가 이 순수 함수를 똑같이 호출해
F-11 대조에서 같은 계산 경로를 보장한다.

좌표계
- Velodyne: x 전방, y 좌측, z 위 (m). 격자 집계(F-6)에 사용
- 카메라 rect: x 우측, y 아래, z 전방 (m). 라벨 location과 박스 판정(F-8)에 사용
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from . import config


# --------------------------------------------------------------------------
# 좌표 변환
# --------------------------------------------------------------------------
def velo_to_rect(points_xyz: np.ndarray, calib: Mapping[str, np.ndarray]) -> np.ndarray:
    """Velodyne 좌표 (N,3)를 카메라 rect 좌표 (N,3) float64로 변환한다.

    X_rect = R0_rect · Tr_velo_to_cam · [x y z 1]ᵀ
    """
    pts = np.asarray(points_xyz, dtype=np.float64)[:, :3]
    tr = np.asarray(calib["Tr_velo_to_cam"], dtype=np.float64)  # (3,4)
    r0 = np.asarray(calib["R0_rect"], dtype=np.float64)  # (3,3)
    cam = pts @ tr[:, :3].T + tr[:, 3]
    return cam @ r0.T


def rect_to_velo(points_rect: np.ndarray, calib: Mapping[str, np.ndarray]) -> np.ndarray:
    """카메라 rect 좌표 (N,3)를 Velodyne 좌표 (N,3) float64로 되돌린다(``velo_to_rect``의 역변환).

    X_velo = Tr[:, :3]⁻¹ · (R0_rect⁻¹ · X_rect − Tr[:, 3])
    """
    pts = np.asarray(points_rect, dtype=np.float64)[:, :3]
    tr = np.asarray(calib["Tr_velo_to_cam"], dtype=np.float64)
    r0 = np.asarray(calib["R0_rect"], dtype=np.float64)
    cam = np.linalg.solve(r0, pts.T).T
    return np.linalg.solve(tr[:, :3], (cam - tr[:, 3]).T).T


def box_footprint_rect(l: float, w: float, x: float, y: float, z: float, ry: float) -> np.ndarray:
    """3D 박스 바닥면 꼭짓점 4개 (4,3)을 rect 좌표로. ``points_in_box_mask``와 같은 박스 정의.

    박스 좌표 (lx, lz) = (±l/2, ±w/2)를 dx = cos(ry)·lx + sin(ry)·lz, dz = −sin(ry)·lx + cos(ry)·lz로
    되돌린다(``points_in_box_mask``의 회전의 역). 바닥면이므로 y = location y.
    """
    c, s = np.cos(float(ry)), np.sin(float(ry))
    half_l, half_w = float(l) / 2.0, float(w) / 2.0
    local = np.array([[half_l, half_w], [half_l, -half_w], [-half_l, -half_w], [-half_l, half_w]])
    dx = c * local[:, 0] + s * local[:, 1]
    dz = -s * local[:, 0] + c * local[:, 1]
    return np.column_stack([float(x) + dx, np.full(4, float(y)), float(z) + dz])


# --------------------------------------------------------------------------
# 객체 속성
# --------------------------------------------------------------------------
def is_valid_3d(cls: str, h: float, w: float, l: float, z: float) -> bool:
    """3D 박스 유효 여부: DontCare가 아니고 h, w, l > 0 이고 location z > 0.

    NaN은 비교가 거짓이므로 무효가 된다. DB 과목 gt_object.valid_3d와 같은 규칙.
    """
    return bool(cls != config.DONTCARE and h > 0 and w > 0 and l > 0 and z > 0)


def distance_m(x: Any, z: Any) -> Any:
    """카메라 좌표 기준 수평거리 √(x² + z²) (m). 스칼라·배열 모두 가능."""
    xa = np.asarray(x, dtype=np.float64)
    za = np.asarray(z, dtype=np.float64)
    out = np.sqrt(xa * xa + za * za)
    return float(out) if out.ndim == 0 else out


def dist_bin(dist: Any) -> Any:
    """거리 구간 라벨(DB fn_dist_bin과 동일): <10 '0-10', <20 '10-20', <40 '20-40', ≥40 '40+'.

    스칼라면 str(NaN·None이면 None), 배열이면 object 배열을 돌려준다.
    """
    if dist is None:
        return None
    d = np.asarray(dist, dtype=np.float64)
    labels = np.array(config.DIST_BIN_LABELS, dtype=object)
    idx = np.searchsorted(np.asarray(config.DIST_BIN_EDGES), d, side="right")
    out = np.where(np.isnan(d), None, labels[np.minimum(idx, len(labels) - 1)])
    return out.item() if out.ndim == 0 else out


def bbox_height(bbox_top: Any, bbox_bottom: Any) -> Any:
    """2D 박스 높이 bottom − top (px)."""
    out = np.asarray(bbox_bottom, dtype=np.float64) - np.asarray(bbox_top, dtype=np.float64)
    return float(out) if out.ndim == 0 else out


def difficulty(
    bbox_h: Any, occluded: Any, truncated: Any, is_dontcare: Any = False
) -> dict[str, Any]:
    """KITTI 공식 난이도 적격 여부와 diff_level을 계산한다.

    기준(Easy / Moderate / Hard): 2D 박스 높이 ≥ 40/25/25 px, occluded ≤ 0/1/2,
    truncated ≤ 0.15/0.30/0.50. 등급은 누적이라 eligible_moderate == (diff_level ≤ 2).
    diff_level: 1 Easy, 2 Moderate, 3 Hard, 4 해당 없음.

    높이 비교는 `>=`(경계 포함, config.DIFF_HEIGHT_COMPARISON)로 DB 과목 diff_level과 같다.
    KITTI 공식 devkit 평가는 엄격한 `>`를 쓰므로 bbox 높이가 정확히 40/25 px인 객체(학습 세트 실제
    객체 config.DIFF_DEVKIT_EXACT_BOUNDARY_OBJECTS = 17개)는 devkit과 등급이 다를 수 있다.
    README 요구(DB 과목과 일관성)에 따라 `>=`를 유지한다.

    DontCare는 truncated·occluded가 무효 표식(-1)이므로 모든 등급에서 부적격(4)이다.
    DB 과목에서 무효 표식을 NULL로 적재해 diff_level이 NULL이 되는 것과 같은 결과다.

    입력이 스칼라면 bool/int, 배열이면 배열을 돌려준다.
    """
    bh = np.asarray(bbox_h, dtype=np.float64)
    occ = np.asarray(occluded, dtype=np.float64)
    trunc = np.asarray(truncated, dtype=np.float64)
    usable = ~np.asarray(is_dontcare, dtype=bool)

    flags = []
    for min_h, max_occ, max_trunc in zip(
        config.DIFF_MIN_BBOX_HEIGHT, config.DIFF_MAX_OCCLUDED, config.DIFF_MAX_TRUNCATED
    ):
        flags.append(usable & (bh >= min_h) & (occ <= max_occ) & (trunc <= max_trunc))
    easy, moderate, hard = flags
    level = np.where(easy, 1, np.where(moderate, 2, np.where(hard, 3, config.DIFF_NONE)))

    if np.ndim(level) == 0:
        return {
            "eligible_easy": bool(easy),
            "eligible_moderate": bool(moderate),
            "eligible_hard": bool(hard),
            "diff_level": int(level),
        }
    return {
        "eligible_easy": easy,
        "eligible_moderate": moderate,
        "eligible_hard": hard,
        "diff_level": level.astype(np.int32),
    }


# --------------------------------------------------------------------------
# 3D 박스 내부 포인트 수 (F-8)
# --------------------------------------------------------------------------
def points_in_box_mask(
    points_rect: np.ndarray,
    h: float,
    w: float,
    l: float,
    x: float,
    y: float,
    z: float,
    ry: float,
    eps: float = config.EPS,
) -> np.ndarray:
    """rect 좌표 점 (N,3)이 3D 박스 안(경계 포함, 여유 eps)에 있는지 bool (N,).

    d = p − location, lx = cos(ry)·dx − sin(ry)·dz, lz = sin(ry)·dx + cos(ry)·dz, ly = dy
    내부: |lx| ≤ l/2 + ε, |lz| ≤ w/2 + ε, −h − ε ≤ ly ≤ ε  (location은 박스 바닥 중심, y 아래 방향)
    """
    p = np.asarray(points_rect, dtype=np.float64)
    dx = p[:, 0] - float(x)
    dy = p[:, 1] - float(y)
    dz = p[:, 2] - float(z)
    c = np.cos(float(ry))
    s = np.sin(float(ry))
    lx = c * dx - s * dz
    lz = s * dx + c * dz
    return (
        (np.abs(lx) <= float(l) / 2.0 + eps)
        & (np.abs(lz) <= float(w) / 2.0 + eps)
        & (dy >= -float(h) - eps)
        & (dy <= eps)
    )


def points_in_box_counts(
    points_rect: np.ndarray,
    objects: Sequence[Mapping[str, Any]],
    eps: float = config.EPS,
) -> list[int | None]:
    """객체마다 3D 박스 내부 포인트 수를 센다. 무효 객체(DontCare 포함)는 None.

    Args:
        points_rect: 카메라 rect 좌표 점 (N,3), float64 권장.
        objects: parse_label 결과처럼 class, h, w, l, x, y, z, ry 키를 가진 매핑.

    Returns:
        objects와 같은 순서의 포인트 수 목록(정수 또는 None).
    """
    pts = np.asarray(points_rect, dtype=np.float64)
    counts: list[int | None] = []
    for obj in objects:
        if not is_valid_3d(obj["class"], obj["h"], obj["w"], obj["l"], obj["z"]):
            counts.append(None)
            continue
        mask = points_in_box_mask(
            pts, obj["h"], obj["w"], obj["l"], obj["x"], obj["y"], obj["z"], obj["ry"], eps
        )
        counts.append(int(np.count_nonzero(mask)))
    return counts


# --------------------------------------------------------------------------
# BEV 격자 집계 (F-6)
# --------------------------------------------------------------------------
def grid_index(points_velo: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Velodyne 점의 격자 번호와 ROI 마스크를 계산한다.

    float32 → float64 변환 → ROI·NaN/Inf 필터 → grid_x = floor((x − 0.0)/1.0),
    grid_y = floor((y + 40.0)/1.0) (np.floor). Spark SQL은 CAST(x AS DOUBLE) 후 FLOOR로 같게 계산.
    z가 NaN/Inf인 점도 뺀다(z_sum·z_min·z_max가 NaN/Inf로 오염되지 않게. analysis.GRID_SQL과 같은 규칙).

    Returns:
        (mask, grid_x, grid_y). grid_x/grid_y는 mask가 참인 점만 담은 int32 배열.
    """
    pts = np.asarray(points_velo)
    x = pts[:, 0].astype(np.float64)
    y = pts[:, 1].astype(np.float64)
    z = pts[:, 2].astype(np.float64)
    # ROI·유한값 필터를 floor보다 먼저 적용한다(spec Critic 5).
    mask = (
        np.isfinite(x)
        & np.isfinite(y)
        & np.isfinite(z)
        & (x >= config.ROI_X_MIN)
        & (x < config.ROI_X_MAX)
        & (y >= config.ROI_Y_MIN)
        & (y < config.ROI_Y_MAX)
    )
    gx = np.floor((x[mask] - config.ROI_X_MIN) / config.GRID_CELL).astype(np.int32)
    gy = np.floor((y[mask] - config.ROI_Y_MIN) / config.GRID_CELL).astype(np.int32)
    return mask, gx, gy


def grid_cell_counts(points_velo: np.ndarray) -> dict[str, np.ndarray]:
    """ROI 안 점을 1 m 격자로 집계한다. 비어 있지 않은 격자만 (grid_x, grid_y) 오름차순.

    Returns:
        grid_x int32, grid_y int32, n_points int64,
        z_sum / z_min / z_max float64 (Velodyne z, m).
    """
    pts = np.asarray(points_velo)
    mask, gx, gy = grid_index(pts)
    z = pts[mask, 2].astype(np.float64)
    if gx.size == 0:
        empty_i = np.empty(0, dtype=np.int32)
        empty_f = np.empty(0, dtype=np.float64)
        return {
            "grid_x": empty_i,
            "grid_y": empty_i.copy(),
            "n_points": np.empty(0, dtype=np.int64),
            "z_sum": empty_f,
            "z_min": empty_f.copy(),
            "z_max": empty_f.copy(),
        }
    key = gx.astype(np.int64) * config.GRID_NY + gy
    order = np.argsort(key, kind="stable")
    key_sorted = key[order]
    z_sorted = z[order]
    starts = np.flatnonzero(np.r_[True, key_sorted[1:] != key_sorted[:-1]])
    uniq = key_sorted[starts]
    n_points = np.diff(np.r_[starts, key_sorted.size]).astype(np.int64)
    return {
        "grid_x": (uniq // config.GRID_NY).astype(np.int32),
        "grid_y": (uniq % config.GRID_NY).astype(np.int32),
        "n_points": n_points,
        "z_sum": np.add.reduceat(z_sorted, starts),
        "z_min": np.minimum.reduceat(z_sorted, starts),
        "z_max": np.maximum.reduceat(z_sorted, starts),
    }


# --------------------------------------------------------------------------
# F-2 범위 검사 (spec Critic 8)
# --------------------------------------------------------------------------
_LABEL_NUMERIC = (
    "truncated", "occluded", "alpha", "bbox_left", "bbox_top", "bbox_right",
    "bbox_bottom", "h", "w", "l", "x", "y", "z", "ry",
)


def object_range_errors(
    obj: Mapping[str, Any],
    image_w: int | None = None,
    image_h: int | None = None,
) -> list[str]:
    """일반 객체의 범위 오류 사유 코드 목록(빈 목록 = 정상).

    DontCare는 3D 값이 원래 무효 표식이므로 검사하지 않고 항상 빈 목록을 돌려준다
    (DontCare 건수는 품질 보고서에 따로 센다). 이미지 크기를 모르면 이미지 경계 검사는 건너뛴다.

    사유 코드: non_finite, truncated_range([0,1]), occluded_range({0,1,2,3}),
    alpha_range / ry_range([-π, π]), bbox_order(left<right, top<bottom),
    bbox_outside_image(0 ≤ 좌표 ≤ 이미지 크기).
    """
    if obj["class"] == config.DONTCARE:
        return []
    reasons: list[str] = []
    if not all(np.isfinite(float(obj[k])) for k in _LABEL_NUMERIC):
        reasons.append("non_finite")
    if not 0.0 <= obj["truncated"] <= 1.0:
        reasons.append("truncated_range")
    if obj["occluded"] not in (0, 1, 2, 3):
        reasons.append("occluded_range")
    if not -np.pi <= obj["alpha"] <= np.pi:
        reasons.append("alpha_range")
    if not -np.pi <= obj["ry"] <= np.pi:
        reasons.append("ry_range")
    left, top, right, bottom = (
        obj["bbox_left"], obj["bbox_top"], obj["bbox_right"], obj["bbox_bottom"]
    )
    if not (left < right and top < bottom):
        reasons.append("bbox_order")
    if image_w is not None and image_h is not None:
        inside = 0.0 <= left <= image_w and 0.0 <= right <= image_w
        inside = inside and 0.0 <= top <= image_h and 0.0 <= bottom <= image_h
        if not inside:
            reasons.append("bbox_outside_image")
    return reasons


def point_range_masks(points_velo: np.ndarray) -> dict[str, np.ndarray]:
    """포인트별 범위 오류 bool 마스크 (N,). 참 = 오류.

    non_finite: x, y, z, reflectance 중 NaN/Inf, coord_range: |x|,|y|,|z| > 200 m,
    reflectance_range: 반사 강도가 [0, 1] 밖, any: 위 셋 중 하나라도 참.
    비교는 float64로 변환해 수행한다. 오류 포인트도 point_id 유지를 위해 지우지 않는다.
    """
    p = np.asarray(points_velo).astype(np.float64)
    xyz = p[:, :3]
    refl = p[:, 3]
    non_finite = ~np.isfinite(p).all(axis=1)
    with np.errstate(invalid="ignore"):
        coord_range = np.isfinite(xyz).all(axis=1) & (np.abs(xyz) > config.POINT_ABS_MAX).any(axis=1)
        reflectance_range = np.isfinite(refl) & ((refl < 0.0) | (refl > 1.0))
    return {
        "non_finite": non_finite,
        "coord_range": coord_range,
        "reflectance_range": reflectance_range,
        "any": non_finite | coord_range | reflectance_range,
    }
