"""레이아웃 검토용 **데모 데이터** (실제 분석 결과가 아님).

S1·S2·S6 화면 구성을 MySQL·KITTI 원본 없이 확인하려고 만든 고정 fixture다.
``KITTI_DEMO=1``(또는 앱 설정 ``KITTI_DEMO=True``)일 때만 쓰며, 기본은 실제 데이터다.
화면에는 항상 ‘데모 데이터’ 표시가 함께 나간다. S5는 데모 경로 없이 항상 결과 파일을 읽는다.

- 모든 값은 ``frame_id``를 시드로 한 결정적 난수로 만든다(새로고침해도 같음).
- 숫자는 README 9.4 와이어프레임의 자리표시 값을 따른다. 실제 값이 아니다.
- 이 모듈의 계산(투영·난이도·포인트 생성)은 데모 생성용이다. 실제 화면은 DB 과목
  NR-1에 따라 필터·집계를 MySQL이 맡는다.
"""

from __future__ import annotations

import math
import random
from array import array

IS_DEMO = True

IMAGE_W, IMAGE_H = 1242, 375
# KITTI P2 근사값 (데모 투영용)
_FX, _CX, _CY = 721.5377, 609.5593, 172.854
SENSOR_HEIGHT_M = 1.73
#: BEV 관심 영역 (데모). 실제 값은 F-6 구현 시 확정해 같은 값을 쓴다.
BEV_ROI = {"fwd_min": 0.0, "fwd_max": 80.0, "left_min": -40.0, "left_max": 40.0}

#: 데모 관측 부족 기준(클래스별 포인트 수). 실제 값은 analysis_threshold에서 온다.
DEMO_THRESHOLDS = {
    "Car": 48, "Van": 40, "Truck": 60, "Pedestrian": 12,
    "Person_sitting": 10, "Cyclist": 14, "Tram": 80, "Misc": 20,
}
DEMO_THRESHOLD_COMPUTED_AT = "데모 · 산출 전"

_DIMS = {  # (h, w, l) m
    "Car": (1.53, 1.63, 3.88), "Van": (2.2, 1.9, 5.1), "Truck": (3.2, 2.6, 10.0),
    "Pedestrian": (1.76, 0.66, 0.84), "Person_sitting": (1.27, 0.54, 0.80),
    "Cyclist": (1.74, 0.6, 1.76), "Tram": (3.5, 2.6, 15.0), "Misc": (1.9, 1.5, 3.6),
}
_CLASS_WEIGHTS = [
    ("Car", 46), ("Pedestrian", 20), ("Cyclist", 8), ("Van", 7), ("Truck", 5),
    ("Misc", 4), ("Person_sitting", 3), ("Tram", 2),
]


# ---------------------------------------------------------------------------
# 장면·객체
# ---------------------------------------------------------------------------
def _project(x: float, y: float, z: float) -> tuple[float, float]:
    return _FX * x / z + _CX, _FX * y / z + _CY


def _difficulty(h_px: float, occ: int, trunc: float) -> int | None:
    if h_px >= 40 and occ <= 0 and trunc <= 0.15:
        return 1
    if h_px >= 25 and occ <= 1 and trunc <= 0.30:
        return 2
    if h_px >= 25 and occ <= 2 and trunc <= 0.50:
        return 3
    return None


def _make_object(rng: random.Random, oid: int, cls: str, x: float, z: float,
                 occ: int, ry: float) -> dict | None:
    h, w, l = _DIMS[cls]
    y = SENSOR_HEIGHT_M - 0.08
    c, s = math.cos(ry), math.sin(ry)
    corners_xz = []
    for dx, dz in ((l / 2, w / 2), (l / 2, -w / 2), (-l / 2, -w / 2), (-l / 2, w / 2)):
        corners_xz.append((x + c * dx + s * dz, z - s * dx + c * dz))
    us, vs = [], []
    for cx, cz in corners_xz:
        if cz < 1.0:
            return None
        for cy in (y, y - h):
            u, v = _project(cx, cy, cz)
            us.append(u)
            vs.append(v)
    left, right = min(us), max(us)
    top, bottom = min(vs), max(vs)
    raw_w = right - left
    cl, cr = max(0.0, left), min(float(IMAGE_W), right)
    ct, cb = max(0.0, top), min(float(IMAGE_H), bottom)
    if cr - cl < 4 or cb - ct < 4:
        return None
    trunc = round(max(0.0, 1 - (cr - cl) / raw_w), 2) if raw_w > 0 else 0.0
    if trunc > 0.6:
        return None
    dist = math.hypot(x, z)
    # 데모용 포인트 수: 거리 제곱에 반비례 + 가림 감쇠 + 잡음
    base = {"Car": 9000, "Van": 11000, "Truck": 16000, "Tram": 20000}.get(cls, 2200)
    pts = base / max(dist, 3.0) ** 2 * 25 * (1 - 0.3 * min(occ, 2)) * rng.uniform(0.6, 1.3)
    num_points = int(max(0, round(pts)))
    h_px = cb - ct
    return {
        "id": oid,
        "cls": cls,
        "dist_m": round(dist, 1),
        "occluded": occ,
        "truncated": trunc,
        "num_points": num_points,
        "valid_3d": True,
        "diff_level": _difficulty(h_px, occ, trunc),
        "low_obs": num_points < DEMO_THRESHOLDS[cls],
        "threshold": DEMO_THRESHOLDS[cls],
        "bbox": [round(cl, 1), round(ct, 1), round(cr, 1), round(cb, 1)],
        "dims": [h, w, l],
        "loc": [round(x, 2), round(y, 2), round(z, 2)],
        "ry": round(ry, 2),
        # BEV 좌표계(앞 = 카메라 z, 왼쪽 = -카메라 x)의 바닥면 꼭짓점
        "footprint": [[round(cz, 2), round(-cx, 2)] for cx, cz in corners_xz],
        "verdict": None,
    }


def _scene_objects(frame_id: int) -> list[dict]:
    rng = random.Random(frame_id * 7919 + 17)
    n = rng.randint(4, 8)
    names, weights = zip(*_CLASS_WEIGHTS)
    objs: list[dict] = []
    oid = 1
    tries = 0
    while len(objs) < n and tries < 200:
        tries += 1
        cls = rng.choices(names, weights)[0]
        z = rng.uniform(6, 62)
        lane = rng.choice([-7.5, -3.6, 0.0, 3.6, 7.5])
        if cls in ("Pedestrian", "Person_sitting", "Cyclist"):
            lane = rng.choice([-9.0, -6.5, 6.0, 8.5])
        x = lane + rng.uniform(-1.0, 1.0)
        if any(abs(o["loc"][0] - x) < 2.5 and abs(o["loc"][2] - z) < 6 for o in objs):
            continue
        ry = rng.choice([-math.pi / 2, math.pi / 2]) + rng.uniform(-0.2, 0.2)
        occ = rng.choices([0, 1, 2, 3], [50, 30, 15, 5])[0]
        obj = _make_object(rng, oid, cls, x, z, occ, ry)
        if obj:
            objs.append(obj)
            oid += 1
    objs.sort(key=lambda o: o["dist_m"])
    for i, o in enumerate(objs, start=1):
        o["id"] = i
    # 상태 다양화: 원거리 하나는 관측 부족, 하나는 3D 박스 무효(NULL), 판정 하나
    if len(objs) >= 3:
        far = objs[-1]
        far["num_points"] = max(1, far["threshold"] // 4)
        far["low_obs"] = True
    if len(objs) >= 5:
        bad = objs[-2]
        bad["num_points"] = None
        bad["valid_3d"] = False
        bad["low_obs"] = False
        bad["footprint"] = None
    if objs:
        objs[0]["verdict"] = "ok"
    # 무시 영역(DontCare) — 2D 박스만 있고 3D 값은 무효
    dc_l = rng.uniform(420, 760)
    objs.append({
        "id": len(objs) + 1, "cls": "DontCare", "dist_m": None, "occluded": None,
        "truncated": None, "num_points": None, "valid_3d": False, "diff_level": None,
        "low_obs": False, "threshold": None,
        "bbox": [round(dc_l, 1), 168.0, round(dc_l + rng.uniform(40, 90), 1), 192.0],
        "dims": None, "loc": None, "ry": None, "footprint": None, "verdict": None,
    })
    return objs


def get_scene(frame_id: int) -> dict:
    """장면 상세(S2) 데모. 실제로는 MySQL 상세 프로시저 + 원본 파일."""
    objects = _scene_objects(frame_id)
    real = [o for o in objects if o["cls"] != "DontCare"]
    pos = result_position(frame_id)
    return {
        "frame_id": frame_id,
        "objects": objects,
        "object_count": len(real),
        "dontcare_count": len(objects) - len(real),
        "image_size": [IMAGE_W, IMAGE_H],
        "roi": BEV_ROI,
        "position": pos,
        "is_demo": IS_DEMO,
        "threshold_computed_at": DEMO_THRESHOLD_COMPUTED_AT,
    }


# ---------------------------------------------------------------------------
# 검색 결과 (S1)
# ---------------------------------------------------------------------------
DEMO_FRAME_IDS = [
    123, 1045, 2210, 87, 3391, 4402, 512, 6120, 7003, 2789, 1876, 5544,
    330, 4128, 6871, 919, 2457, 3802, 5011, 7420, 1503, 2999, 6300, 4777,
]
DEMO_TOTAL_SCENES = 1284
DEMO_TOTAL_OBJECTS = 2031
DEMO_PAGE_SIZE = 24


def search_results() -> dict:
    """S1 결과 데모. 실제로는 ``CALL sp_search_scenes_ex(…)`` 결과 집합."""
    cards = []
    for fid in DEMO_FRAME_IDS:
        objs = [o for o in _scene_objects(fid) if o["cls"] != "DontCare"]
        rng = random.Random(fid)
        match = [o for o in objs if o["cls"] == "Pedestrian"] or objs[:1]
        cards.append({
            "frame_id": fid,
            "match_objects": len(match),
            "match_classes": [match[0]["cls"]],
            "n_objects": len(objs),
            "nearest_m": min(o["dist_m"] for o in match),
            "low_obs": sum(1 for o in objs if o["low_obs"]),
            "review_status": rng.choices(["pending", "reviewed", "disputed"], [6, 3, 1])[0],
            "objects": objs,
        })
    return {
        "cards": cards,
        "total_scenes": DEMO_TOTAL_SCENES,
        "total_objects": DEMO_TOTAL_OBJECTS,
        "page_size": DEMO_PAGE_SIZE,
        "pages": math.ceil(DEMO_TOTAL_SCENES / DEMO_PAGE_SIZE),
        "relax_hint": {"field": "거리", "count": 312},
        "is_demo": IS_DEMO,
    }


def result_position(frame_id: int) -> dict | None:
    """검색 결과 안에서의 위치(12 / 1,284)와 이전·다음 장면."""
    if frame_id not in DEMO_FRAME_IDS:
        return None
    i = DEMO_FRAME_IDS.index(frame_id)
    return {
        "index": i + 1,
        "total": DEMO_TOTAL_SCENES,
        "prev": DEMO_FRAME_IDS[i - 1] if i > 0 else None,
        "next": DEMO_FRAME_IDS[i + 1] if i + 1 < len(DEMO_FRAME_IDS) else None,
    }


DEMO_PRESETS = [
    {"key": "far_ped", "label": "원거리 · 일부 가림 · 보행자"},
    {"key": "low_obs_cyc", "label": "관측 부족 자전거"},
    {"key": "near_heavy_occ", "label": "근거리 · 대부분 가림 차량"},
]


# ---------------------------------------------------------------------------
# 점군 (float32 x, y, z, 반사 강도 — LiDAR 좌표계: x 앞, y 왼쪽, z 위)
# ---------------------------------------------------------------------------
def demo_points(frame_id: int) -> bytes:
    """관심 영역 안의 데모 점군을 ``float32`` 리틀 엔디언 바이너리로 만든다."""
    rng = random.Random(frame_id * 31 + 3)
    out = array("f")
    ground = -SENSOR_HEIGHT_M
    roi = BEV_ROI
    # 지면 링: 가까울수록 촘촘
    r = 3.2
    while r < 78:
        n = int(1800 / (1 + r / 12))
        for _ in range(n):
            a = rng.uniform(-math.pi / 2.2, math.pi / 2.2)
            x, y = r * math.cos(a), r * math.sin(a)
            if roi["fwd_min"] <= x <= roi["fwd_max"] and roi["left_min"] <= y <= roi["left_max"]:
                out.extend((x, y, ground + rng.gauss(0, 0.03), rng.uniform(0.05, 0.35)))
        r *= 1.075
    # 길가 벽·건물
    for side in (1, -1):
        off = rng.uniform(11, 15) * side
        x = rng.uniform(2, 6)
        while x < 70:
            seg = rng.uniform(4, 14)
            hgt = rng.uniform(2, 7)
            for _ in range(int(seg * 30 / (1 + x / 25))):
                px = x + rng.uniform(0, seg)
                py = off + rng.gauss(0, 0.15)
                out.extend((px, py, ground + rng.uniform(0, hgt), rng.uniform(0.1, 0.6)))
            x += seg + rng.uniform(1, 6)
    # 객체 내부 점
    for o in _scene_objects(frame_id):
        if not o["footprint"] or not o["num_points"]:
            continue
        h = o["dims"][0]
        fp = o["footprint"]
        cx = sum(p[0] for p in fp) / 4
        cy = sum(p[1] for p in fp) / 4
        # 바닥면 두 변 벡터
        ax, ay = (fp[1][0] - fp[0][0]) / 2, (fp[1][1] - fp[0][1]) / 2
        bx, by = (fp[3][0] - fp[0][0]) / 2, (fp[3][1] - fp[0][1]) / 2
        for _ in range(min(o["num_points"], 2500)):
            u, v = rng.uniform(-0.95, 0.95), rng.uniform(-0.95, 0.95)
            out.extend((cx + u * ax + v * bx, cy + u * ay + v * by,
                        ground + rng.uniform(0.05, h), rng.uniform(0.2, 0.9)))
    return out.tobytes()
