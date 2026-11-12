"""S1·S2·S6 실제 데이터 조회 (README 9.4, DB 과목 NR-1).

필터·집계·건수는 모두 MySQL 프로시저(db/03_views_procs.sql)가 계산한다. 이 모듈은
- 화면 조건을 프로시저 인자로 바꾸고,
- 프로시저 결과를 템플릿이 쓰는 dict로 옮기고,
- 선택한 장면의 원본 파일(이미지·.bin·calib)을 읽어 표시용 좌표로 바꾼다.
행을 거르거나 다시 세지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from kitti import config as kcfg
from kitti import geometry
from kitti import io as kio

from .db import Database

PAGE_SIZE = 24
#: 가림 기본 집합 = sp_search_scenes의 기본 범위 0~2
OCC_DEFAULT_SET = (0, 1, 2)
FRAME_ID_MIN, FRAME_ID_MAX = 0, kcfg.EXPECTED_TRAINING_FRAMES - 1

#: BEV 관심 영역 = F-6 격자와 같은 Velodyne 좌표 ROI (앞 = x, 왼쪽 = y)
BEV_ROI = {
    "fwd_min": kcfg.ROI_X_MIN, "fwd_max": kcfg.ROI_X_MAX,
    "left_min": kcfg.ROI_Y_MIN, "left_max": kcfg.ROI_Y_MAX,
}


def valid_frame_id(frame_id: int) -> bool:
    return FRAME_ID_MIN <= frame_id <= FRAME_ID_MAX


def frame_file(training: Path, subdir: str, frame_id: int) -> Path:
    """검증한 정수 frame_id로만 경로를 만든다(경로 조작 불가)."""
    return Path(training) / subdir / f"{int(frame_id):06d}{kcfg.SUBDIRS[subdir]}"


# ---------------------------------------------------------------------------
# S1 검색
# ---------------------------------------------------------------------------
def class_ids_for(db: Database, classes: list[str]) -> list[int]:
    """클래스 이름 목록 → object_class.class_id 목록 (sp_class_ids). 빈 목록이면 부르지 않는다."""
    if not classes:
        return []
    return [int(r["class_id"]) for r in db.call("sp_class_ids", json.dumps(classes))[0]]


def search_params(state: dict, class_ids: list[int]) -> tuple:
    """화면 조건 → sp_search_scenes_ex의 앞 8개 필터 인자 (정렬·p_limit·p_offset 제외).

    클래스·가림 집합은 JSON 배열로 넘긴다. 클래스를 고르지 않았으면 NULL(모든 클래스),
    가림이 기본 집합(0·1·2)이면 NULL(프로시저 기본 0~2)이다.
    """
    occ = None if state["occ"] == list(OCC_DEFAULT_SET) else json.dumps(state["occ"])
    return (
        json.dumps(class_ids) if class_ids else None,
        state["dmin"],
        state["dmax"],
        occ,
        state["pmin"],
        state["pmax"],
        1 if state["low_obs"] else 0,
        1 if state["unreviewed"] else 0,
    )


def _split_classes(value: str | None) -> list[str]:
    return [c for c in (value or "").split(",") if c]


def search(db: Database, state: dict) -> dict:
    """S1 결과: 목록은 sp_search_scenes_ex, 결과 수·완화 제안은 sp_search_summary, 카드 정보는 sp_scene_cards."""
    params = search_params(state, class_ids_for(db, state["cls"]))
    totals_rows, relax_rows = db.call("sp_search_summary", *params)
    total_scenes = int(totals_rows[0]["total_scenes"] or 0)
    total_objects = int(totals_rows[0]["total_objects"] or 0)
    pages = max(1, -(-total_scenes // PAGE_SIZE))
    page = min(state["page"], pages)
    offset = (page - 1) * PAGE_SIZE

    rows = (db.call("sp_search_scenes_ex", *params, state["sort"], PAGE_SIZE, offset)[0]
            if total_scenes else [])
    info: dict[int, dict] = {}
    if rows:
        frames = json.dumps([int(r["frame_id"]) for r in rows])
        info = {int(r["frame_id"]): r for r in db.call("sp_scene_cards", *params, frames)[0]}

    cards = []
    for i, r in enumerate(rows):
        fid = int(r["frame_id"])
        extra = info.get(fid, {})
        cards.append({
            "frame_id": fid,
            "index": offset + i + 1,
            "match_objects": int(r["matched_objects"]),
            "min_points": None if r["min_points"] is None else int(r["min_points"]),
            "match_classes": _split_classes(extra.get("match_classes")),
            "nearest_m": None if r["nearest_m"] is None else float(r["nearest_m"]),
            "farthest_m": None if r["farthest_m"] is None else float(r["farthest_m"]),
            "low_obs": int(extra.get("n_low") or 0),
            "n_objects": int(extra.get("n_objects") or 0),
            "review_status": None,
        })
    relax = None
    if relax_rows:
        relax = {"cond": relax_rows[0]["cond"], "count": int(relax_rows[0]["scenes"] or 0)}
    return {
        "cards": cards,
        "page": page,
        "total_scenes": total_scenes,
        "total_objects": total_objects,
        "page_size": PAGE_SIZE,
        "pages": pages,
        "relax": relax,
        "params": params,
        "is_demo": False,
    }


def position(db: Database, state: dict, index: int, frame_id: int) -> dict | None:
    """검색 결과 안에서의 위치(12 / 1,284)와 이전·다음 장면.

    S1 카드 링크가 넘긴 순번(index, 1부터)의 앞뒤 3행을 같은 프로시저·같은 정렬로 다시 받아, 그 자리에
    이 장면이 있을 때만 위치를 보여준다(데이터가 바뀌었으면 위치를 숨긴다).
    """
    if index < 1:
        return None
    params = search_params(state, class_ids_for(db, state["cls"]))
    offset = max(index - 2, 0)
    ids = [int(r["frame_id"]) for r in db.call("sp_search_scenes_ex", *params, state["sort"], 3, offset)[0]]
    k = index - 1 - offset
    if k >= len(ids) or ids[k] != frame_id:
        return None
    total = int(db.call("sp_search_summary", *params)[0][0]["total_scenes"] or 0)
    return {
        "index": index,
        "total": total,
        "prev": ids[k - 1] if k > 0 else None,
        "next": ids[k + 1] if k + 1 < len(ids) else None,
    }


# ---------------------------------------------------------------------------
# S2 장면 상세
# ---------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def load_calib(training: Path, frame_id: int) -> dict[str, np.ndarray] | None:
    try:
        return kio.parse_calib(frame_file(training, "calib", frame_id))
    except (OSError, kio.KittiParseError):
        return None


def footprint_velo(obj: dict, calib: dict[str, np.ndarray]) -> list[list[float]]:
    """3D 박스 바닥면 꼭짓점을 BEV 좌표 [[앞(m), 왼쪽(m)] × 4]로 (보정 행렬로 rect → Velodyne)."""
    rect = geometry.box_footprint_rect(obj["dim_l"], obj["dim_w"], obj["loc_x"], obj["loc_y"],
                                       obj["loc_z"], obj["rotation_y"])
    velo = geometry.rect_to_velo(rect, calib)
    return [[round(float(p[0]), 3), round(float(p[1]), 3)] for p in velo]


def scene_detail(db: Database, frame_id: int, training: Path) -> dict | None:
    """sp_scene_detail 결과 + 원본 파일 상태. 장면이 DB에 없으면 None."""
    frame_rows, obj_rows = db.call("sp_scene_detail", frame_id)
    if not frame_rows:
        return None
    fr = frame_rows[0]
    calib = load_calib(training, frame_id)
    objects = []
    for r in obj_rows:
        is_dc = bool(r["is_ignore"])
        valid = bool(r["valid_3d"]) if r["valid_3d"] is not None else False
        footprint = footprint_velo(r, calib) if (valid and not is_dc and calib is not None) else None
        objects.append({
            "id": int(r["object_id"]),
            "cls": r["class_name"],
            "dist_m": _num(r["dist_m"]),
            "occluded": None if r["occluded"] is None else int(r["occluded"]),
            "truncated": _num(r["truncated"]),
            "num_points": None if r["points_in_box"] is None else int(r["points_in_box"]),
            "valid_3d": valid,
            "diff_level": None if r["diff_level"] is None else int(r["diff_level"]),
            "low_obs": bool(r["is_low"]),
            "threshold": None if r["low_points"] is None else int(r["low_points"]),
            "bbox": [float(r["bbox_left"]), float(r["bbox_top"]), float(r["bbox_right"]), float(r["bbox_bottom"])],
            "footprint": footprint,
            "verdict": None,
        })
    real = [o for o in objects if o["cls"] != "DontCare"]
    image = frame_file(training, "image_2", frame_id)
    velo = frame_file(training, "velodyne", frame_id)
    calib_path = frame_file(training, "calib", frame_id)
    computed = fr.get("threshold_at")
    return {
        "frame_id": frame_id,
        "objects": objects,
        "object_count": len(real),
        "dontcare_count": len(objects) - len(real),
        "image_size": [int(fr["image_w"] or 1242), int(fr["image_h"] or 375)],
        "n_points": None if fr["n_points"] is None else int(fr["n_points"]),
        "roi": BEV_ROI,
        "position": None,
        "is_demo": False,
        "threshold_computed_at": computed.strftime("%Y-%m-%d %H:%M") if hasattr(computed, "strftime") else (computed or "—"),
        "image_missing": None if image.is_file() else image.name,
        "lidar_missing": None if velo.is_file() else velo.name,
        "calib_missing": None if calib is not None else calib_path.name,
    }


def roi_points(training: Path, frame_id: int) -> bytes | None:
    """관심 영역 안의 점만 float32 리틀 엔디언 (x, y, z, 반사 강도)로 (README 9.4 ‘점군 전송’).

    ROI는 F-6 격자와 같다: x ∈ [0, 80), y ∈ [-40, 40). 파일이 없거나 읽을 수 없으면 None.
    """
    path = frame_file(training, "velodyne", frame_id)
    try:
        pts = kio.read_velodyne(path)
    except (OSError, kio.KittiParseError):
        return None
    x, y = pts[:, 0], pts[:, 1]
    keep = (np.isfinite(pts).all(axis=1)
            & (x >= kcfg.ROI_X_MIN) & (x < kcfg.ROI_X_MAX)
            & (y >= kcfg.ROI_Y_MIN) & (y < kcfg.ROI_Y_MAX))
    return np.ascontiguousarray(pts[keep], dtype="<f4").tobytes()


# ---------------------------------------------------------------------------
# S6 기준값
# ---------------------------------------------------------------------------
def thresholds(db: Database) -> tuple[dict[str, int], str]:
    """analysis_threshold (sp_thresholds) → ({클래스: 기준값}, 산출 일시)."""
    rows = db.call("sp_thresholds")[0]
    values = {r["class_name"]: int(r["low_points"]) for r in rows}
    # sp_refresh_threshold가 모든 클래스를 한 트랜잭션에서 같은 NOW()로 쓴다.
    computed = rows[0]["computed_at"] if rows else None
    at = computed.strftime("%Y-%m-%d %H:%M") if hasattr(computed, "strftime") else "—"
    return values, at
