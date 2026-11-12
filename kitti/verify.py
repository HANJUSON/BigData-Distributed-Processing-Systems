"""Spark 결과 ↔ Python 순차 처리 결과 대조 (README F-11, 7.5). pyspark를 쓰지 않는다.

대조 규칙 (README F-11)
- 키 집합 동일, 양쪽 모두 키 중복 0건, NULL 위치 완전 일치
- 정수 필드(객체 수, 격자 n_points, points_in_box, 기준값, 추출 목록 등) 오차 0
  (추출 목록의 포함 여부는 키 집합 일치로 판정)
- 실수 필드(z_sum, 평균, dist_m 등) `abs(a-b) <= 1e-6 + 1e-6*abs(b)` (a = Spark, b = 순차)
- 문자열·논리 필드는 완전 일치
비교는 키로 정렬한 Arrow/numpy 배열로 한다(pyarrow.compute 조인·정렬, numpy 마스크).

입력
- Spark: `--spark-work`(KITTI_WORK. `parquet/object_features`, `parquet/grid_stats`. 측정 실행 폴더나
  analysis 부분집합 폴더(<work>/subsets/<name>)처럼 `object_features/`, `grid_stats/`가 바로 아래 있어도
  된다)과 `--spark-results`(analysis.py 결과 파일)
- 순차: `--seq` (kitti.sequential 출력 폴더). 순차 표는 `<seq>/<표 이름>`에서만 읽는다.

추가 규칙
- 장면 집합: 양쪽 grid_params.json의 frame_set(n, sha256)이 있어야 하고 같아야 한다. 다르거나 없으면
  `frame_set` 항목이 실패하고 나머지 항목은 대조하지 않는다(부분 필터링으로 맞추지 않음).
  `--frames-file`을 주면 그 목록의 해시도 양쪽과 같아야 한다(같을 때만 통과, 거르는 일은 없음).
- 빈 대조 금지: rows_compared == 0이면 실패. 예외로 f9_low_objects·f9_low_scenes는 같은 실행의
  f9_thresholds가 1행 이상 대조됐을 때만 비어 있어도 된다(기준값이 있는데 목록이 빈 경우는 실제로는
  생기지 않지만, 판정을 기준값 대조에 묶어 "둘 다 비어서 통과"를 막는다).
- 자기 대조 금지: 모든 항목에서 Spark·순차 원본 경로(resolve 후)가 달라야 한다.
- grid_stats·object_features는 frame_id 구간별로 나눠 읽어 대조한다(키에 frame_id가 있어 구간을 넘는
  중복·누락은 없으므로 규칙은 전체 대조와 같다). 메모리는 구간 하나 분량이다.

출력: <out>/verification.json, <out>/verification.md, <out>/units.md

CLI:
    python -m kitti.verify --spark-work ~/data/kitti/work --spark-results results --seq DIR --out results
                           [--frames-file samples/sample_100.txt]
실패가 하나라도 있으면 종료 코드 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ABS_TOL: float = 1e-6
REL_TOL: float = 1e-6
MAX_EXAMPLES: int = 5
CHUNK_FRAMES: int = 256
"""Parquet 표(grid_stats·object_features)를 나눠 대조할 frame_id 구간 폭."""
EMPTY_OK_IF: dict[str, str] = {"f9_low_objects": "f9_thresholds", "f9_low_scenes": "f9_thresholds"}
"""rows_compared == 0을 허용하는 항목 → 그 조건이 되는 항목(1행 이상 대조돼야 함)."""

# 필드 종류: key(키, 완전 일치) / int(정수 오차 0) / float(허용 오차) / str·bool(완전 일치)
# (이름, 종류, 원래 dtype, 단위, 설명)
Field = tuple[str, str, str, str, str]

_OBJ_FIELDS: list[Field] = [
    ("frame_id", "key", "int32", "-", "장면 ID"),
    ("object_id", "key", "int32", "-", "label_2 원본 줄 번호(1부터)"),
    ("class", "str", "string", "-", "객체 종류"),
    ("truncated", "float", "float64", "ratio (0-1)", "잘림 비율, DontCare -1"),
    ("occluded", "int", "int32", "level (0-3)", "가림 정도, DontCare -1"),
    ("alpha", "float", "float64", "rad", "관측 각도"),
    ("bbox_left", "float", "float64", "px", "2D 박스 왼쪽"),
    ("bbox_top", "float", "float64", "px", "2D 박스 위쪽"),
    ("bbox_right", "float", "float64", "px", "2D 박스 오른쪽"),
    ("bbox_bottom", "float", "float64", "px", "2D 박스 아래쪽"),
    ("h", "float", "float64", "m", "3D 높이"),
    ("w", "float", "float64", "m", "3D 너비"),
    ("l", "float", "float64", "m", "3D 길이"),
    ("x", "float", "float64", "m", "박스 바닥 중심 x (카메라 rect 좌표)"),
    ("y", "float", "float64", "m", "박스 바닥 중심 y (카메라 rect 좌표, 아래 +)"),
    ("z", "float", "float64", "m", "박스 바닥 중심 z (카메라 rect 좌표, 전방)"),
    ("ry", "float", "float64", "rad", "rotation_y"),
    ("is_dontcare", "bool", "bool", "-", "class == DontCare"),
    ("valid_3d", "bool", "bool", "-", "h,w,l > 0 and z > 0 and not DontCare"),
    ("dist_m", "float", "float64", "m", "카메라 좌표 수평거리 sqrt(x²+z²), DontCare NULL"),
    ("bbox_height", "float", "float64", "px", "2D 박스 높이"),
    ("eligible_easy", "bool", "bool", "-", "KITTI Easy 적격"),
    ("eligible_moderate", "bool", "bool", "-", "KITTI Moderate 적격"),
    ("eligible_hard", "bool", "bool", "-", "KITTI Hard 적격"),
    ("diff_level", "int", "int32", "level (1-4)", "난이도 등급"),
    ("points_in_box", "int", "int32", "count (points)", "3D 박스 내부 LiDAR 포인트 수, 무효 3D NULL"),
    ("dist_bin", "str", "string", "-", "거리 구간 (<10, <20, <40, ≥40 m)"),
]

_GRID_FIELDS: list[Field] = [
    ("frame_id", "key", "int32", "-", "장면 ID"),
    ("grid_x", "key", "int32", "cell (1 m)", "floor((x-0)/1.0), Velodyne x"),
    ("grid_y", "key", "int32", "cell (1 m)", "floor((y+40)/1.0), Velodyne y"),
    ("n_points", "int", "int64", "count (points)", "격자 안 포인트 수"),
    ("z_sum", "float", "float64", "m", "격자 안 Velodyne z 합"),
    ("z_min", "float", "float64", "m", "격자 안 Velodyne z 최솟값"),
    ("z_max", "float", "float64", "m", "격자 안 Velodyne z 최댓값"),
]

_COUNT3: list[Field] = [
    ("n_objects", "int", "int64", "count (objects)", "객체 수"),
    ("n_valid_3d", "int", "int64", "count (objects)", "유효 3D 객체 수"),
    ("sum_points_in_box", "int", "int64", "count (points)", "박스 내부 포인트 합, 전부 NULL이면 NULL"),
]

# 항목 정의: name, spark 위치, seq 위치, 형식, 필드
# 위치: ("work", 표 이름) = Parquet 표 / ("results", 파일) / ("f5", 파일) / ("params", 파일)
ITEMS: list[dict[str, Any]] = [
    {"name": "object_features", "title": "객체 특징 표 (F-7·F-8)", "spark": ("work", "object_features"),
     "seq": ("work", "object_features"), "fields": _OBJ_FIELDS},
    {"name": "grid_stats", "title": "장면별 격자 통계 (F-6)", "spark": ("work", "grid_stats"),
     "seq": ("work", "grid_stats"), "fields": _GRID_FIELDS},
    {"name": "f5_per_class", "title": "클래스별 요약 (F-5)", "spark": ("f5", "f5_dataframe_vs_sql.json"),
     "seq": ("f5", "f5_per_class.json"),
     "fields": [("class", "key", "string", "-", "객체 종류(DontCare 포함)")] + _COUNT3[:2] + [
         ("mean_dist_m", "float", "float64", "m", "AVG(CAST(dist_m AS DECIMAL(20,12))) → DOUBLE"),
         ("n_scenes", "int", "int64", "count (frames)", "객체가 있는 장면 수"),
         _COUNT3[2],
     ]},
    {"name": "grid_cumulative", "title": "전체 누적 격자 (F-6)", "spark": ("results", "grid_cumulative.csv"),
     "seq": ("results", "grid_cumulative.csv"),
     "fields": [
         ("grid_x", "key", "int64", "cell (1 m)", "격자 x 번호"),
         ("grid_y", "key", "int64", "cell (1 m)", "격자 y 번호"),
         ("x_min_m", "float", "float64", "m", "격자 x 하한"),
         ("y_min_m", "float", "float64", "m", "격자 y 하한"),
         ("n_points_sum", "int", "int64", "count (points)", "모든 프레임 합"),
         ("n_points_mean", "float", "float64", "points / frame", "합 / 정상 처리 프레임 수, 빈 격자 0"),
     ]},
    {"name": "grid_params", "title": "정상 처리 프레임 수 (F-6 평균 분모)", "spark": ("params", "grid_params.json"),
     "seq": ("params", "grid_params.json"),
     "fields": [("param", "key", "string", "-", "항목 이름"),
                ("value", "int", "int64", "count (frames)", "n_frames_processed")]},
    {"name": "f7_class_dist", "title": "클래스 × 거리 구간 (F-7)", "spark": ("results", "f7_class_dist.csv"),
     "seq": ("results", "f7_class_dist.csv"),
     "fields": [("class", "key", "string", "-", "객체 종류"), ("dist_bin", "key", "string", "-", "거리 구간")]
     + _COUNT3},
    {"name": "f7_class_occluded", "title": "클래스 × 가림 (F-7)", "spark": ("results", "f7_class_occluded.csv"),
     "seq": ("results", "f7_class_occluded.csv"),
     "fields": [("class", "key", "string", "-", "객체 종류"), ("occluded", "key", "int32", "level (0-3)", "가림")]
     + _COUNT3},
    {"name": "f7_class_truncated", "title": "클래스 × 잘림 구간 (F-7)", "spark": ("results", "f7_class_truncated.csv"),
     "seq": ("results", "f7_class_truncated.csv"),
     "fields": [("class", "key", "string", "-", "객체 종류"),
                ("trunc_bucket", "key", "string", "ratio bucket", "잘림 구간('invalid' 포함)")] + _COUNT3},
    {"name": "f7_class_difficulty", "title": "클래스별 난이도 적격 (F-7)",
     "spark": ("results", "f7_class_difficulty.csv"), "seq": ("results", "f7_class_difficulty.csv"),
     "fields": [("class", "key", "string", "-", "객체 종류"), _COUNT3[0]] + [
         (f"n_{k}", "int", "int64", "count (objects)", d) for k, d in (
             ("eligible_easy", "Easy 적격 수"), ("eligible_moderate", "Moderate 적격 수"),
             ("eligible_hard", "Hard 적격 수"), ("not_eligible", "diff_level = 4 수"))
     ]},
    {"name": "f9_thresholds", "title": "클래스별 관측 부족 기준값 (F-9)", "spark": ("results", "f9_thresholds.csv"),
     "seq": ("results", "f9_thresholds.csv"),
     "fields": [("class", "key", "string", "-", "객체 종류"),
                ("n", "int", "int64", "count (objects)", "유효 3D 객체 수"),
                ("k", "int", "int32", "rank", "ceil(n/10)"),
                ("threshold", "int", "int32", "count (points)", "오름차순 k번째 points_in_box")]},
    {"name": "f9_low_objects", "title": "관측 부족 객체 목록 (F-9)", "spark": ("results", "f9_low_objects.csv"),
     "seq": ("results", "f9_low_objects.csv"),
     "fields": [
         ("frame_id", "key", "int32", "-", "장면 ID"),
         ("object_id", "key", "int32", "-", "객체 줄 번호"),
         ("class", "str", "string", "-", "객체 종류"),
         ("points_in_box", "int", "int32", "count (points)", "박스 내부 포인트 수"),
         ("threshold", "int", "int32", "count (points)", "클래스 기준값"),
         ("dist_m", "float", "float64", "m", "수평거리"),
         ("dist_bin", "str", "string", "-", "거리 구간"),
         ("occluded", "int", "int32", "level (0-3)", "가림"),
         ("truncated", "float", "float64", "ratio (0-1)", "잘림"),
         ("diff_level", "int", "int32", "level (1-4)", "난이도 등급"),
         ("h", "float", "float64", "m", "3D 높이"),
         ("w", "float", "float64", "m", "3D 너비"),
         ("l", "float", "float64", "m", "3D 길이"),
     ]},
    {"name": "f9_low_scenes", "title": "관측 부족 장면 목록 (F-9)", "spark": ("results", "f9_low_scenes.csv"),
     "seq": ("results", "f9_low_scenes.csv"),
     "fields": [("frame_id", "key", "int32", "-", "장면 ID"),
                ("n_low_objects", "int", "int64", "count (objects)", "장면의 관측 부족 객체 수"),
                ("classes", "str", "string", "-", "관측 부족 클래스(정렬, ';' 연결)")]},
]

SHARED_FUNCTION_NOTE: str = (
    "object_features의 points_in_box·dist_m·난이도 열(eligible_easy/moderate/hard, diff_level, dist_bin)은 "
    "양쪽 모두 같은 순수 함수(kitti.features.features_from_arrays)로 계산한다. 따라서 F-11은 이 열들에 대해 "
    "Spark 배관(장면 분할·직렬화·쓰기·집계)을 검증할 뿐 기하 계산 자체를 독립적으로 검증하지 않는다. "
    "F-8 기하의 독립 증거는 `results/f8_independent_check.json`(devkit 꼭짓점 + 면 반공간 재계산), "
    "`results/figures/f8_visual_check.png`(시각 점검), MySQL 생성 열 대조(`results/mysql_consistency.json`의 "
    "dist_m·diff_level·eligible_*·dist_bin 모순 0건)다."
)
"""verification.md·units.md에 넣는 F-11 범위 안내(같은 함수를 쓰는 열)."""

_KIND_KO = {"key": "키", "int": "정수", "float": "실수", "str": "문자열", "bool": "논리"}
_RULE = {
    "key": "키 집합 일치·중복 0",
    "int": "오차 0",
    "float": f"abs(a-b) ≤ {ABS_TOL:g} + {REL_TOL:g}·abs(b)",
    "str": "완전 일치",
    "bool": "완전 일치",
}


# --------------------------------------------------------------------------
# 읽기
# --------------------------------------------------------------------------
def _arrow_type(kind: str, dtype: str) -> pa.DataType:
    """대조용 공통 자료형. CSV에서 정수 열은 NULL이 있으면 '12.0'으로 써지므로 float64로 읽는다."""
    if kind == "key":
        return pa.string() if dtype == "string" else pa.int64()
    return {"int": pa.float64(), "float": pa.float64(), "str": pa.string(), "bool": pa.bool_()}[kind]


def _normalize(table: pa.Table, fields: Sequence[Field]) -> pa.Table:
    """비교할 열만 정해진 순서·공통 자료형으로 바꾼다. 정수는 float64로 바꿔도 2^53까지 정확하다."""
    cols = []
    for name, kind, dtype, _, _ in fields:
        if name not in table.column_names:
            raise KeyError(f"column '{name}' missing")
        col = table.column(name)
        target = pa.int64() if kind == "int" and pa.types.is_integer(col.type) else _arrow_type(kind, dtype)
        cols.append(pc.cast(col, target))
    return pa.table(cols, names=[f[0] for f in fields]).combine_chunks()


def _work_table(base: Path, name: str, nested: bool = True) -> Path:
    """Parquet 표 경로. nested면 base/parquet/name을 먼저 본다(Spark 쪽). 순차 쪽은 base/name만."""
    cands = (base / "parquet" / name, base / name) if nested else (base / name,)
    for cand in cands:
        if cand.exists():
            return cand
    raise FileNotFoundError(f"{name} not found under {base}")


def _source_path(kind: str, ref: str, work: Path, results: Path, nested: bool = True) -> Path:
    if kind == "work":
        return _work_table(work, ref, nested)
    path = results / ref
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path


def _read_source(kind: str, ref: str, work: Path, results: Path, fields: Sequence[Field],
                 frame_ids: pa.Array | None) -> tuple[pa.Table, str]:
    if kind == "work":
        path = _work_table(work, ref)
        table = pq.read_table(path)
        if frame_ids is not None:
            table = table.filter(pc.is_in(table.column("frame_id"), value_set=frame_ids))
        return table, str(path)
    path = results / ref
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if kind == "results":
        types = {n: _arrow_type(k, d) for n, k, d, _, _ in fields}
        table = pcsv.read_csv(path, convert_options=pcsv.ConvertOptions(column_types=types))
        return table, str(path)
    obj = json.loads(path.read_text(encoding="utf-8"))
    if kind == "f5":
        rows = obj["per_class_rows"]
        schema = pa.schema([(n, _arrow_type(k, d)) for n, k, d, _, _ in fields])
        return pa.Table.from_pylist(rows, schema=schema), str(path)
    # params
    return pa.table({"param": ["n_frames_processed"], "value": [int(obj["n_frames_processed"])]}), str(path)


# --------------------------------------------------------------------------
# 대조
# --------------------------------------------------------------------------
def _with_index(table: pa.Table) -> pa.Table:
    return table.append_column("__row", pa.array(np.arange(table.num_rows, dtype=np.int64)))


def _duplicates(table: pa.Table, keys: list[str]) -> pa.Table:
    """2번 이상 나오는 키와 횟수."""
    if table.num_rows == 0:
        return table.select(keys).append_column("count_all", pa.array([], pa.int64()))
    agg = table.group_by(keys, use_threads=False).aggregate([([], "count_all")])
    return agg.filter(pc.greater(agg.column("count_all"), 1))


def _dedupe(table: pa.Table, keys: list[str]) -> pa.Table:
    """키마다 첫 행만 남긴다(중복 보고 후 나머지 열 비교용)."""
    if table.num_rows == 0:
        return table
    t = _with_index(table)
    first = t.group_by(keys, use_threads=False).aggregate([("__row", "min")]).column("__row_min")
    return table.take(pa.array(np.sort(first.to_numpy()), pa.int64()))


_NULL_KEY_INT: int = -(2**63)
_NULL_KEY_STR: str = "\x00<NULL>"


def _join_keys(table: pa.Table, keys: list[str]) -> pa.Table:
    """조인용 키 열. Arrow 조인은 NULL 키끼리 맞추지 않으므로 NULL을 실제 값에 없는 표지값으로 바꾼다
    (그래야 양쪽 같은 위치의 NULL 키가 '양쪽에만 있음'으로 잘못 보고되지 않는다)."""
    cols = []
    for k in keys:
        col = table.column(k)
        fill = _NULL_KEY_STR if pa.types.is_string(col.type) else _NULL_KEY_INT
        cols.append(pc.fill_null(col, pa.scalar(fill, col.type)) if col.null_count else col)
    return pa.table(cols, names=keys)


def _anti(a: pa.Table, b: pa.Table, keys: list[str]) -> pa.Table:
    """a에만 있는 키."""
    ak = a.select(keys)
    if a.num_rows == 0 or b.num_rows == 0:
        return ak
    kept = _with_index(_join_keys(a, keys)).join(_join_keys(b, keys), keys, join_type="left anti",
                                                  use_threads=False)
    return ak.take(pa.array(np.sort(kept.column("__row").to_numpy()), pa.int64()))


def _semi(a: pa.Table, b: pa.Table, keys: list[str]) -> pa.Table:
    """a 중 b에도 있는 키의 행(원래 순서)."""
    if a.num_rows == 0 or b.num_rows == 0:
        return a.slice(0, 0)
    kept = _with_index(_join_keys(a, keys)).join(_join_keys(b, keys), keys, join_type="left semi",
                                                  use_threads=False)
    return a.take(pa.array(np.sort(kept.column("__row").to_numpy()), pa.int64()))


def _sort(table: pa.Table, keys: list[str]) -> pa.Table:
    return table.sort_by([(k, "ascending") for k in keys])


def _examples(table: pa.Table, mask: np.ndarray, cols: list[str]) -> list[dict[str, Any]]:
    idx = np.flatnonzero(mask)[:MAX_EXAMPLES]
    return table.select(cols).take(pa.array(idx, pa.int64())).to_pylist() if idx.size else []


def compare_tables(a: pa.Table, b: pa.Table, fields: Sequence[Field]) -> dict[str, Any]:
    """a(Spark)와 b(순차)를 F-11 규칙으로 대조한다. 두 표는 _normalize를 거친 것이어야 한다."""
    keys = [f[0] for f in fields if f[1] == "key"]
    res: dict[str, Any] = {"keys": keys, "rows_spark": a.num_rows, "rows_seq": b.num_rows}
    dup_a, dup_b = _duplicates(a, keys), _duplicates(b, keys)
    res["duplicate_keys_spark"] = dup_a.num_rows
    res["duplicate_keys_seq"] = dup_b.num_rows
    res["duplicate_examples"] = {
        "spark": dup_a.slice(0, MAX_EXAMPLES).to_pylist(),
        "seq": dup_b.slice(0, MAX_EXAMPLES).to_pylist(),
    }
    ua, ub = _dedupe(a, keys), _dedupe(b, keys)
    only_a, only_b = _anti(ua, ub, keys), _anti(ub, ua, keys)
    res["keys_only_in_spark"] = only_a.num_rows
    res["keys_only_in_seq"] = only_b.num_rows
    res["missing_key_examples"] = {
        "only_in_spark": _sort(only_a, keys).slice(0, MAX_EXAMPLES).to_pylist(),
        "only_in_seq": _sort(only_b, keys).slice(0, MAX_EXAMPLES).to_pylist(),
    }

    # 공통 키 행을 키로 정렬해 같은 위치끼리 비교한다.
    ca = _sort(_semi(ua, ub, keys), keys)
    cb = _sort(_semi(ub, ua, keys), keys)
    res["rows_compared"] = ca.num_rows
    columns: dict[str, Any] = {}
    for name, kind, _, _, _ in fields:
        if kind == "key":
            continue
        x, y = ca.column(name), cb.column(name)
        xn = pc.is_null(x).to_numpy(zero_copy_only=False)
        yn = pc.is_null(y).to_numpy(zero_copy_only=False)
        null_bad = xn != yn
        both = ~xn & ~yn
        info: dict[str, Any] = {"kind": kind, "null_mismatch": int(null_bad.sum())}
        if kind == "float":
            xv = pc.fill_null(x, 0.0).to_numpy()
            yv = pc.fill_null(y, 0.0).to_numpy()
            with np.errstate(invalid="ignore"):
                diff = np.abs(xv - yv)
                ok = (diff <= ABS_TOL + REL_TOL * np.abs(yv)) | (xv == yv) | (np.isnan(xv) & np.isnan(yv))
            val_bad = both & ~ok
            finite = both & np.isfinite(diff)
            info["max_abs_diff"] = float(diff[finite].max()) if finite.any() else 0.0
        else:
            eq = pc.fill_null(pc.equal(x, y), False).to_numpy(zero_copy_only=False)
            val_bad = both & ~eq
        info["value_mismatch"] = int(val_bad.sum())
        bad = null_bad | val_bad
        if bad.any():
            ex_a = _examples(ca, bad, keys + [name])
            ex_b = _examples(cb, bad, [name])
            info["examples"] = [
                {**{k: ea[k] for k in keys}, "spark": ea[name], "seq": eb[name]} for ea, eb in zip(ex_a, ex_b)
            ]
        columns[name] = info
    res["columns"] = columns
    res["null_mismatch"] = int(sum(c["null_mismatch"] for c in columns.values()))
    res["value_mismatch"] = int(sum(c["value_mismatch"] for c in columns.values()))
    res["pass"] = _pass(res)
    return res


def _pass(res: dict[str, Any]) -> bool:
    return bool(
        res["duplicate_keys_spark"] == 0 and res["duplicate_keys_seq"] == 0
        and res["keys_only_in_spark"] == 0 and res["keys_only_in_seq"] == 0
        and res["null_mismatch"] == 0 and res["value_mismatch"] == 0
    )


def merge_results(acc: dict[str, Any] | None, res: dict[str, Any]) -> dict[str, Any]:
    """구간별 compare_tables 결과를 더한다(건수 합, 최대 차 최댓값, 예시는 앞에서 MAX_EXAMPLES개)."""
    if acc is None:
        return json.loads(json.dumps(res, default=str))  # 깊은 복사
    for k in ("rows_spark", "rows_seq", "duplicate_keys_spark", "duplicate_keys_seq",
              "keys_only_in_spark", "keys_only_in_seq", "rows_compared", "null_mismatch", "value_mismatch"):
        acc[k] += res[k]
    for group in ("duplicate_examples", "missing_key_examples"):
        for side, ex in res[group].items():
            acc[group][side] = (acc[group][side] + ex)[:MAX_EXAMPLES]
    for name, col in res["columns"].items():
        a = acc["columns"][name]
        a["null_mismatch"] += col["null_mismatch"]
        a["value_mismatch"] += col["value_mismatch"]
        if "max_abs_diff" in col:
            a["max_abs_diff"] = max(a["max_abs_diff"], col["max_abs_diff"])
        if col.get("examples"):
            a["examples"] = (a.get("examples", []) + col["examples"])[:MAX_EXAMPLES]
    acc["pass"] = _pass(acc)
    return acc


def _frame_bounds(path: Path) -> tuple[int, int] | None:
    """Parquet 표(파일 또는 폴더)의 frame_id 최솟값·최댓값. row group 통계를 쓰고 없으면 열만 읽는다."""
    lo = hi = None
    dataset = ds.dataset(str(path), format="parquet")
    for f in dataset.files:
        meta = pq.ParquetFile(f).metadata
        if meta.num_rows == 0:
            continue
        idx = meta.schema.to_arrow_schema().get_field_index("frame_id")
        for i in range(meta.num_row_groups):
            rg = meta.row_group(i)
            if rg.num_rows == 0:
                continue
            st = rg.column(idx).statistics
            if st is None or not st.has_min_max:
                col = pq.read_table(f, columns=["frame_id"]).column("frame_id")
                st_lo, st_hi = pc.min(col).as_py(), pc.max(col).as_py()
            else:
                st_lo, st_hi = st.min, st.max
            lo = st_lo if lo is None else min(lo, st_lo)
            hi = st_hi if hi is None else max(hi, st_hi)
    return None if lo is None else (int(lo), int(hi))


def compare_parquet_chunked(
    a_path: Path, b_path: Path, fields: Sequence[Field],
    frame_ids: pa.Array | None = None, chunk_frames: int = CHUNK_FRAMES,
) -> dict[str, Any]:
    """두 Parquet 표를 frame_id 구간 [lo, lo+chunk_frames)별로 읽어 compare_tables 결과를 합친다.

    frame_id가 키에 들어 있으므로 키 중복·누락은 구간 안에서만 생기고, 결과는 전체를 한 번에
    대조한 것과 같다. 필터는 row group 통계로 밀어 넣어 구간에 해당하는 row group만 읽는다.
    frame_ids가 있으면 양쪽 모두에 같은 필터를 적용한다.
    """
    da, db = ds.dataset(str(a_path), format="parquet"), ds.dataset(str(b_path), format="parquet")
    cols = [f[0] for f in fields]
    bounds = [b for b in (_frame_bounds(a_path), _frame_bounds(b_path)) if b is not None]
    if not bounds:
        empty = (_normalize(da.schema.empty_table().select(cols), fields),
                 _normalize(db.schema.empty_table().select(cols), fields))
        return compare_tables(*empty, fields)
    lo, hi = min(b[0] for b in bounds), max(b[1] for b in bounds)
    acc: dict[str, Any] | None = None
    for start in range(lo, hi + 1, max(int(chunk_frames), 1)):
        flt = (ds.field("frame_id") >= start) & (ds.field("frame_id") < start + chunk_frames)
        if frame_ids is not None:
            flt = flt & ds.field("frame_id").isin(frame_ids)
        a = _normalize(da.to_table(columns=cols, filter=flt), fields)
        b = _normalize(db.to_table(columns=cols, filter=flt), fields)
        acc = merge_results(acc, compare_tables(a, b, fields))
    assert acc is not None
    return acc


def _frame_set_of(params_path: Path) -> dict[str, Any] | None:
    obj = json.loads(params_path.read_text(encoding="utf-8"))
    fs = obj.get("frame_set")
    return fs if isinstance(fs, dict) and "sha256" in fs else None


def check_frame_sets(
    spark_results: Path, seq_dir: Path, frame_ids: Sequence[int] | None
) -> dict[str, Any]:
    """양쪽 grid_params.json의 frame_set이 기록돼 있고 같은지(frames-file이 있으면 그것과도 같은지)."""
    item: dict[str, Any] = {"name": "frame_set", "title": "대조 장면 집합 동일 (n, sha256)"}
    try:
        sides = {"spark": _frame_set_of(spark_results / "grid_params.json"),
                 "seq": _frame_set_of(seq_dir / "grid_params.json")}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return {**item, "pass": False, "error": _display_path(f"{type(exc).__name__}: {exc}")}
    item.update(sides)
    missing = [k for k, v in sides.items() if v is None]
    if missing:
        return {**item, "pass": False, "error": f"frame_set not recorded in grid_params.json of {missing}; "
                "re-run analysis/sequential with the current code"}
    if frame_ids is not None:
        item["frames_file"] = _frame_set_info(frame_ids)
        sides["frames_file"] = item["frames_file"]
    hashes = {k: v["sha256"] for k, v in sides.items()}
    if len(set(hashes.values())) != 1:
        desc = ", ".join(f"{k}: n={sides[k]['n']} sha256={h[:12]}" for k, h in hashes.items())
        return {**item, "pass": False,
                "error": f"frame sets differ ({desc}); both sides must be produced for the same frame set"}
    return {**item, "pass": True}


def _display_path(text: Path | str) -> str:
    """보고서에 남길 경로·메시지: 홈 폴더 접두어를 `~`로 바꾼다(kitti.config.display_path와 같은 규칙,
    verify는 kitti 패키지 의존 없이 단독 실행 가능하게 둔다). 읽는 쪽은 expanduser()로 되돌린다."""
    text = str(text)
    home = str(Path.home())
    if home in ("", "/"):
        return text
    if text == home:
        return "~"
    return text.replace(home + "/", "~/")


def _frame_set_info(frame_ids: Sequence[int]) -> dict[str, Any]:
    """config.frame_set_info와 같은 계산(verify는 kitti 패키지 의존 없이 단독 실행 가능하게 둔다)."""
    ids = sorted({int(f) for f in frame_ids})
    return {"n": len(ids), "sha256": hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()}


def verify(
    spark_work: Path | str,
    spark_results: Path | str,
    seq_dir: Path | str,
    frame_ids: Sequence[int] | None = None,
    *,
    chunk_frames: int = CHUNK_FRAMES,
) -> dict[str, Any]:
    """모든 대조 항목을 실행해 결과 dict를 돌려준다(파일은 쓰지 않는다).

    맨 앞 `frame_set` 항목이 실패하면(장면 집합 불일치·미기록) 나머지 항목은 대조하지 않고 실패로 둔다.
    """
    spark_work, spark_results, seq_dir = (Path(p).expanduser() for p in (spark_work, spark_results, seq_dir))
    ids = pa.array(sorted({int(f) for f in frame_ids}), pa.int32()) if frame_ids is not None else None
    fs_item = check_frame_sets(spark_results, seq_dir, frame_ids)
    items: list[dict[str, Any]] = [fs_item]
    for spec in ITEMS:
        fields = spec["fields"]
        item: dict[str, Any] = {"name": spec["name"], "title": spec["title"]}
        if not fs_item["pass"]:
            item.update({"pass": False, "error": "not compared: frame_set check failed"})
            items.append(item)
            continue
        try:
            pa_path = _source_path(*spec["spark"], spark_work, spark_results)
            pb_path = _source_path(*spec["seq"], seq_dir, seq_dir, nested=False)
            item["spark_source"], item["seq_source"] = _display_path(pa_path), _display_path(pb_path)
            if pa_path.resolve() == pb_path.resolve():
                raise ValueError(f"spark and sequential sources are the same path: {pa_path.resolve()}")
            if spec["spark"][0] == "work":
                item.update(compare_parquet_chunked(pa_path, pb_path, fields, ids, chunk_frames))
            else:
                a, _ = _read_source(*spec["spark"], spark_work, spark_results, fields, None)
                b, _ = _read_source(*spec["seq"], seq_dir, seq_dir, fields, None)
                item.update(compare_tables(_normalize(a, fields), _normalize(b, fields), fields))
        except (FileNotFoundError, KeyError, ValueError, pa.ArrowInvalid, json.JSONDecodeError) as exc:
            item.update({"pass": False, "error": _display_path(f"{type(exc).__name__}: {exc}")})
        items.append(item)
    by_name = {i["name"]: i for i in items}
    # 클래스마다 오름차순 k번째 객체는 그 자체로 기준값 이하이므로, 기준값이 있으면 관측 부족 객체는
    # 클래스 수(= f9_thresholds 대조 행 수) 이상이어야 한다.
    n_thr = by_name.get("f9_thresholds", {}).get("rows_compared") or 0
    low = by_name.get("f9_low_objects", {})
    low_ok = not n_thr or (low.get("rows_compared") or 0) >= n_thr
    if n_thr and not low_ok and low.get("pass"):
        low["pass"] = False
        low["error"] = (f"f9_low_objects compared {low.get('rows_compared', 0)} rows < f9_thresholds "
                        f"{n_thr} rows (each class's k-th smallest object is itself low)")
    for item in items:
        if item.get("rows_compared") == 0 and item["pass"]:
            cond = EMPTY_OK_IF.get(item["name"])
            if cond is None or not by_name.get(cond, {}).get("rows_compared") or not low_ok:
                item["pass"] = False
                item["error"] = "no rows compared (empty on both sides)" + (
                    f"; allowed only when {cond} compared > 0 rows and f9_low_objects compared >= "
                    f"{cond} rows" if cond else "")
    failed = [i["name"] for i in items if not i["pass"]]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "spark_work": _display_path(spark_work),
        "spark_results": _display_path(spark_results),
        "seq": _display_path(seq_dir),
        "frames": len(ids) if ids is not None else "all",
        "frame_set": {k: fs_item.get(k) for k in ("spark", "seq")},
        "tolerance": {"abs": ABS_TOL, "rel": REL_TOL, "rule": "abs(a-b) <= abs + rel*abs(b), a=Spark, b=sequential"},
        "items": items,
        "n_items": len(items),
        "failed_items": failed,
        "pass": not failed,
    }


# --------------------------------------------------------------------------
# 보고서
# --------------------------------------------------------------------------
def render_markdown(rep: dict[str, Any]) -> str:
    verdict = "통과" if rep["pass"] else "실패"
    lines = [
        "# F-11 정확성 검증: Spark ↔ Python 순차 처리",
        "",
        f"- 생성 시각(UTC): {rep['generated_at']}",
        f"- Spark 작업 폴더: `{rep['spark_work']}` / 결과 폴더: `{rep['spark_results']}`",
        f"- 순차 결과 폴더: `{rep['seq']}`",
        f"- 대상 장면 수: {rep['frames']}",
        f"- 실수 허용 오차: `abs(a-b) <= {ABS_TOL:g} + {REL_TOL:g} × abs(b)` (a = Spark, b = 순차)",
        f"- **종합 결과: {verdict}** ({rep['n_items'] - len(rep['failed_items'])}/{rep['n_items']} 항목 통과)",
        "",
        f"- 장면 집합: Spark {rep.get('frame_set', {}).get('spark')} / 순차 {rep.get('frame_set', {}).get('seq')}",
        "",
        "## 항목별 결과",
        "",
        "| 항목 | 행 수 (Spark / 순차) | 키 중복 (Spark / 순차) | 키 누락 (Spark만 / 순차만) | NULL 위치 불일치 | 값 불일치 | 최대 실수 차 | 결과 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for it in rep["items"]:
        if "error" in it or "rows_spark" not in it:
            verdict_cell = f"실패: {it['error']}" if "error" in it else ("통과" if it["pass"] else "실패")
            lines.append(f"| {it['title']} (`{it['name']}`) | - | - | - | - | - | - | {verdict_cell} |")
            continue
        maxdiff = max((c.get("max_abs_diff", 0.0) for c in it["columns"].values()), default=0.0)
        lines.append(
            f"| {it['title']} (`{it['name']}`) | {it['rows_spark']:,} / {it['rows_seq']:,} "
            f"| {it['duplicate_keys_spark']} / {it['duplicate_keys_seq']} "
            f"| {it['keys_only_in_spark']} / {it['keys_only_in_seq']} | {it['null_mismatch']} "
            f"| {it['value_mismatch']} | {maxdiff:.3g} | {'통과' if it['pass'] else '실패'} |"
        )
    bad = [it for it in rep["items"] if not it["pass"]]
    if bad:
        lines += ["", "## 불일치 상세", ""]
        for it in bad:
            lines.append(f"### {it['title']} (`{it['name']}`)")
            lines.append("")
            if "error" in it:
                lines += [f"- 오류: {it['error']}", ""]
                continue
            for side, label in (("spark", "Spark"), ("seq", "순차")):
                for ex in it["duplicate_examples"][side]:
                    lines.append(f"- 키 중복({label}): `{ex}`")
            for side, label in (("only_in_spark", "Spark에만"), ("only_in_seq", "순차에만")):
                for ex in it["missing_key_examples"][side]:
                    lines.append(f"- 키 {label} 있음: `{ex}`")
            for col, c in it["columns"].items():
                if c["null_mismatch"] or c["value_mismatch"]:
                    lines.append(
                        f"- `{col}` ({_KIND_KO[c['kind']]}): NULL 위치 불일치 {c['null_mismatch']}, "
                        f"값 불일치 {c['value_mismatch']}"
                    )
                    for ex in c.get("examples", []):
                        lines.append(f"  - `{ex}`")
            lines.append("")
    lines += ["", "## 범위", "", f"- {SHARED_FUNCTION_NOTE}", "",
              "필드별 단위·자료형은 `units.md`를 참고한다.", ""]
    return "\n".join(lines)


def render_units() -> str:
    lines = [
        "# F-11 대조 필드 단위·자료형",
        "",
        "README F-11 \"필드별 단위 기록\". 좌표 변환과 집계는 float64로 계산한다.",
        "자료형은 원래 표(Parquet·Spark 결과)의 자료형이다. CSV에서 읽을 때 NULL이 있는 정수 열은",
        "float64로 읽어 비교한다(2^53 이하 정수는 정확히 표현되므로 '오차 0' 판정에 영향 없음).",
        "",
        f"- 실수 허용 오차: `abs(a-b) <= {ABS_TOL:g} + {REL_TOL:g} × abs(b)` (a = Spark, b = 순차)",
        "- 좌표계: 격자는 Velodyne(x 전방, y 좌측, z 위, 원점 = 센서), 객체 위치·거리는 카메라 rect 좌표",
        "- 격자 필터: x, y, z 중 NaN/Inf인 점과 ROI 밖 점은 FLOOR 전에 뺀다(Spark GRID_SQL·numpy 동일)",
        "- points_in_box: .bin이 없거나 크기 오류인 장면은 모든 객체가 NULL(사이드카 `_skipped_frames.json`)",
        "",
        "## 생성 노트: 난이도 경계 (`eligible_*`, `diff_level`)",
        "",
        "- 2D 박스 높이 기준은 `>=` 40/25 px(경계 포함)로 DB 과목 `diff_level`과 같다(README 요구).",
        "- KITTI 공식 devkit 평가는 엄격한 `>`를 쓴다. 학습 세트에서 높이가 정확히 40 px 또는 25 px인",
        "  실제 객체 17개는 devkit과 등급이 다를 수 있다(동작은 바꾸지 않고 기록만 한다).",
        "",
        "## 같은 함수로 계산하는 열 (F-11 범위)",
        "",
        f"- {SHARED_FUNCTION_NOTE}",
        "",
        "## 빈 대조·장면 집합 규칙",
        "",
        "- 대조 행이 0인 항목은 실패. `f9_low_objects`·`f9_low_scenes`만 `f9_thresholds`가 1행 이상",
        "  대조되고 `f9_low_objects` 대조 행 수가 `f9_thresholds` 행 수 이상일 때 비어 있어도 된다.",
        "- `f9_thresholds`가 있으면 `f9_low_objects` 대조 행 수는 그 행 수 이상이어야 한다(클래스마다",
        "  k번째 객체 자신이 기준값 이하).",
        "- NULL 키는 조인 전에 표지값으로 바꿔 양쪽 같은 NULL 키끼리 맞춘다.",
        "- 양쪽 grid_params.json의 `frame_set`(n, sha256)이 같아야 하며, 다르면 다른 항목은 대조하지 않는다.",
        "",
    ]
    for spec in ITEMS:
        lines += [
            f"## {spec['title']} (`{spec['name']}`)",
            "",
            "| 필드 | 역할 | 자료형 | 단위 | 비교 규칙 | 설명 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for name, kind, dtype, unit, desc in spec["fields"]:
            lines.append(f"| `{name}` | {_KIND_KO[kind]} | {dtype} | {unit} | {_RULE[kind]} | {desc} |")
        lines.append("")
    return "\n".join(lines)


def write_reports(rep: dict[str, Any], out: Path | str) -> None:
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "verification.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out / "verification.md").write_text(render_markdown(rep), encoding="utf-8")
    (out / "units.md").write_text(render_units(), encoding="utf-8")


def render_only(out: Path | str) -> dict[str, Any]:
    """기존 <out>/verification.json에서 verification.md·units.md만 다시 쓴다(대조는 다시 하지 않음)."""
    out = Path(out).expanduser()
    rep = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    (out / "verification.md").write_text(render_markdown(rep), encoding="utf-8")
    (out / "units.md").write_text(render_units(), encoding="utf-8")
    return rep


def _read_ids(path: Path | str) -> list[int]:
    ids: list[int] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        ids.extend(int(tok) for tok in line.split("#", 1)[0].split())
    return ids


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F-11 Spark ↔ 순차 결과 대조")
    parser.add_argument("--spark-work", help="KITTI_WORK 또는 측정 실행 폴더")
    parser.add_argument("--spark-results", help="analysis.py 결과 폴더")
    parser.add_argument("--seq", help="kitti.sequential 출력 폴더")
    parser.add_argument("--out", default="results", help="보고서 폴더")
    parser.add_argument("--frames-file",
                        help="기대 장면 목록. 양쪽 결과의 장면 집합 해시가 이 목록과 같아야 통과")
    parser.add_argument("--render-only", action="store_true",
                        help="대조하지 않고 <out>/verification.json에서 verification.md·units.md만 다시 쓴다")
    args = parser.parse_args(argv)
    if args.render_only:
        rep = render_only(args.out)
        print(f"rendered {Path(args.out) / 'verification.md'} (overall={'PASS' if rep['pass'] else 'FAIL'})")
        return 0 if rep["pass"] else 1
    missing = [f"--{n.replace('_', '-')}" for n in ("spark_work", "spark_results", "seq") if not getattr(args, n)]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    ids = _read_ids(args.frames_file) if args.frames_file else None
    rep = verify(args.spark_work, args.spark_results, args.seq, ids)
    write_reports(rep, args.out)
    for it in rep["items"]:
        print(f"{'PASS' if it['pass'] else 'FAIL'} {it['name']}" + (f" ({it['error']})" if "error" in it else ""))
    print(f"overall={'PASS' if rep['pass'] else 'FAIL'} -> {Path(args.out) / 'verification.md'}")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
