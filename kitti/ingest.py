"""수집·정제 단계 (README F-1~F-4, D-3): 장면 연결 → 품질 검사 → Parquet 변환.

실행:
    python -m kitti.ingest --root ~/data/kitti --work ~/data/kitti/work
                           [--frames-file list.txt --out-parquet DIR --results DIR]
                           [--force-replace] [--allow-incomplete]
                           [--master local[8]] [--partitions 32]

정본 보호 (종료 코드 2로 거부, 아무것도 쓰지 않음)
- 부분집합(--frames-file)인데 출력이 정본 <work>/parquet이거나 품질 보고서 폴더가 기본 results/이면
  --force-replace 없이는 거부한다. 부분집합은 --out-parquet·--results로 다른 폴더에 쓴다.
- 대상 장면(목록 파일의 ID, 없으면 label_2의 모든 ID) 중 4종 파일이 모두 연결된 장면이 모자라거나
  대상 장면에 어떤 종류든 누락 파일이 있으면 --allow-incomplete 없이는 거부한다(교체 안 함).
- 처리한 장면 집합(n, sha256, ids)을 points/_manifest.json과 frames/_manifest.json에 기록한다.
- 교체(swap) 후 같은 Parquet 루트의 파생 표(object_features, grid_stats, low_observation)는
  옛 입력으로 만든 것이므로 지우고 로그에 남긴다.

처리 구조
- F-1 색인(드라이버): 4개 하위 폴더의 파일명(숫자 stem)에서 frame_id를 뽑아 장면 표를 만든다.
  같은 frame_id로 해석되는 파일이 둘 이상이면(예: `000010.bin`과 `10.bin`) 중복으로 보고하고
  6자리 이름 파일을 정본으로 쓴다.
- F-2·F-3·F-4(Spark 작업): frame_id를 정렬해 연속 구간 N개(기본 32)로 나누고, 구간 하나가
  파티션 하나가 된다(mapPartitionsWithIndex). 작업은 맡은 프레임을 오름차순으로 하나씩 읽어
  품질 검사, frames/objects 행 생성, points Parquet 쓰기를 한 뒤 메모리를 놓는다.
  전체 데이터를 한 번에 메모리에 올리지 않는다.
- frames·objects(행 수가 적음)는 작업이 돌려준 튜플을 드라이버에서 kitti.schema의 명시적 스키마로
  DataFrame을 만들어 쓴다.
- points(약 9억 행)는 Python Row 객체를 거치지 않고 작업마다 pyarrow로 파일 하나를 직접 쓴다.
  파일 하나 = 연속 프레임 구간, row group 하나 = 프레임 하나, 파일명 = 프레임 구간
  (`part-00007_f003500-f003733.parquet`). `_manifest.json`이 파일 → 프레임 목록을 기록하므로
  points_files_for()로 부분집합 파일만 고를 수 있다.

정책
- 비정상 포인트(NaN·범위 밖·반사 강도 범위 밖)는 **지우지 않고 그대로 쓴다**. point_id는 원본 .bin
  레코드 순서이며, 오류 건수는 품질 보고서에 사유별로 남긴다. 걸러 쓰는 일은 분석 단계(F-6 ROI·NaN
  필터 등)가 맡는다.
- 크기가 16바이트 배수가 아닌 .bin은 읽지 않고(포인트 0행) 보고서에 남긴다. frames.n_points는 NULL.
- 라벨 파싱 오류가 있는 장면은 objects 행을 만들지 않고 경로·메시지를 보고서에 남긴다.
- 압축: zstd. 좌표 float32 열은 BYTE_STREAM_SPLIT 인코딩, point_id는 DELTA_BINARY_PACKED,
  frame_id는 사전(RLE) 인코딩. 부동소수 잡음이 많은 점군에서 snappy보다 작게 나오고 읽기 비용 차이는 작다.
- 멱등성: 모든 출력은 `parquet/.staging-<run>/` 아래에 쓴 뒤 성공하면 기존 테이블 폴더와 바꾼다.
  다시 실행하면 이전 결과를 깨끗이 덮어쓴다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import config
from . import geometry as geo
from . import io as kio

log = logging.getLogger("kitti.ingest")

REPO_DIR: Path = Path(__file__).resolve().parents[1]
RESULTS_DIR: Path = REPO_DIR / "results"
KINDS: tuple[str, ...] = tuple(config.SUBDIRS)  # image_2, velodyne, label_2, calib
TABLES: tuple[str, ...] = ("frames", "objects", "points")
DERIVED_TABLES: tuple[str, ...] = ("object_features", "grid_stats", "low_observation")
"""ingest 입력에서 파생되는 표. ingest가 정본을 교체하면 낡은 것이 되므로 지운다."""
MANIFEST_NAME: str = "_manifest.json"
POINTS_COMPRESSION: str = "zstd"
"""points(pyarrow)와 frames·objects(Spark) Parquet 압축 코덱."""
MAX_ITEMS: int = 200
"""보고서에 넣는 개별 항목(오류 객체 예시 등) 최대 개수. 건수는 항상 전체를 센다."""

_NUMERIC_STEM = re.compile(r"^\d+$")

# kitti.schema.POINTS와 같은 열·자료형 (pyspark 없이 쓰기 위해 pyarrow로 정의)
POINTS_ARROW_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("frame_id", pa.int32(), nullable=False),
        pa.field("point_id", pa.int32(), nullable=False),
        pa.field("x", pa.float32(), nullable=False),
        pa.field("y", pa.float32(), nullable=False),
        pa.field("z", pa.float32(), nullable=False),
        pa.field("reflectance", pa.float32(), nullable=False),
    ]
)
_CALIB_COLS: tuple[str, ...] = tuple(kio.CALIB_KEYS)  # P0..P3, R0_rect, Tr_velo_to_cam, Tr_imu_to_velo
_POINT_REASONS: tuple[str, ...] = ("non_finite", "coord_range", "reflectance_range")


# --------------------------------------------------------------------------
# 공통
# --------------------------------------------------------------------------
class IngestRefused(RuntimeError):
    """정본 덮어쓰기·불완전 데이터로 실행을 거부할 때(CLI 종료 코드 2)."""


@contextmanager
def _phase(name: str, timings: dict[str, float]) -> Iterator[None]:
    """단계 시간을 재서 로그에 남기고 timings[name]에 초 단위로 기록한다."""
    log.info("[phase] %s ...", name)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round(time.perf_counter() - t0, 3)
        log.info("[phase] %s: %.2f s", name, timings[name])


def _write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# F-1 색인
# --------------------------------------------------------------------------
def build_index(root: Path | str, frame_ids: Iterable[int] | None = None) -> dict[str, Any]:
    """4개 하위 폴더를 훑어 frame_id → 파일 경로 표와 연결 보고를 만든다.

    파일명 stem이 숫자이고 확장자가 맞는 파일만 장면 파일로 본다. 같은 frame_id로 해석되는
    파일이 둘 이상이면 중복으로 기록하고 6자리 이름(없으면 사전순 첫 파일)을 정본으로 쓴다.

    Args:
        root: KITTI 루트(아래에 training/ 이 있음).
        frame_ids: 주어지면 이 장면들만 대상으로 한다(파일이 하나도 없는 ID도 포함해 누락으로 보고).

    Returns:
        frames: {frame_id: {kind: 경로 str 또는 None}} (frame_id 오름차순 dict)
        report: 연결·누락·중복·무시한 파일 요약
    """
    base = config.training_dir(root)
    found: dict[str, dict[int, list[Path]]] = {k: {} for k in KINDS}
    ignored: list[str] = []
    for kind, ext in config.SUBDIRS.items():
        folder = base / kind
        if not folder.is_dir():
            continue
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                stem, dot, suffix = entry.name.rpartition(".")
                if dot and "." + suffix == ext and _NUMERIC_STEM.match(stem):
                    found[kind].setdefault(int(stem), []).append(Path(entry.path))
                else:
                    ignored.append(f"{kind}/{entry.name}")

    all_ids = set().union(*(found[k].keys() for k in KINDS))
    selected = sorted(all_ids if frame_ids is None else {int(f) for f in frame_ids})

    frames: dict[int, dict[str, str | None]] = {}
    duplicates: dict[int, dict[str, list[str]]] = {}
    for fid in selected:
        row: dict[str, str | None] = {}
        for kind in KINDS:
            paths = sorted(found[kind].get(fid, []), key=lambda p: p.name)
            if not paths:
                row[kind] = None
                continue
            canonical = next((p for p in paths if len(p.stem) == 6), paths[0])
            row[kind] = str(canonical)
            if len(paths) > 1:
                duplicates.setdefault(fid, {})[kind] = [str(p) for p in paths]
        frames[fid] = row

    missing = {
        kind: [fid for fid, row in frames.items() if row[kind] is None] for kind in KINDS
    }
    unlinked = [fid for fid, row in frames.items() if any(row[k] is None for k in KINDS)]
    report = {
        "n_frames": len(frames),
        "n_linked": len(frames) - len(unlinked),
        "files_per_kind": {k: sum(1 for r in frames.values() if r[k]) for k in KINDS},
        "missing": {k: {"count": len(v), "ids": v} for k, v in missing.items()},
        "unlinked": {"count": len(unlinked), "ids": unlinked},
        "duplicates": {
            "count": len(duplicates),
            "ids": sorted(duplicates),
            "files": {str(fid): d for fid, d in sorted(duplicates.items())},
        },
        "ignored_files": {"count": len(ignored), "examples": sorted(ignored)[:MAX_ITEMS]},
    }
    return {"frames": frames, "report": report}


def contiguous_chunks(frame_ids: Sequence[int], n_partitions: int) -> list[list[int]]:
    """정렬한 frame_id를 연속 구간 최대 n_partitions개로 나눈다(앞 구간이 1개씩 더 많음)."""
    ids = sorted(frame_ids)
    n = min(n_partitions, len(ids))
    if n == 0:
        return []
    return [list(map(int, c)) for c in np.array_split(np.asarray(ids, dtype=np.int64), n)]


def points_file_name(part: int, first: int, last: int) -> str:
    """points 파일 이름. 파티션 번호와 맡은 프레임 구간(양 끝 포함)을 담는다."""
    return f"part-{part:05d}_f{first:06d}-f{last:06d}.parquet"


# --------------------------------------------------------------------------
# F-2·F-3 프레임 단위 처리 (Spark 작업 안에서 실행)
# --------------------------------------------------------------------------
def _file_size(path: str | None) -> int:
    return os.stat(path).st_size if path else 0


def process_frame(
    frame_id: int, paths: dict[str, str | None], writer_cb: Any = None
) -> dict[str, Any]:
    """장면 하나를 읽어 frames 행, objects 행, 품질 정보를 돌려준다.

    writer_cb(frame_id, points ndarray)가 주어지면 정상 크기 .bin의 포인트를 넘겨 쓰게 한다.
    포인트 배열은 이 함수가 끝나면 버려진다(프레임 단위 스트리밍).
    """
    q: dict[str, Any] = {
        "frame_id": frame_id,
        "errors": [],  # [{kind, path, message}]
        "n_dontcare": 0,
        "object_errors": [],  # [(object_id, [reasons])]
        "invalid_3d": [],  # [object_id]
        "point_reasons": {r: 0 for r in _POINT_REASONS},
        "n_points_with_error": 0,
        "bytes": {k: 0 for k in KINDS},
        "n_points": 0,
        "n_objects": 0,
        "label_parsed": False,
    }

    # 이미지: PNG 헤더만 읽는다(bbox 이미지 경계 검사용 크기).
    image_w = image_h = None
    if paths["image_2"]:
        try:
            image_w, image_h = kio.image_size(paths["image_2"])
            q["bytes"]["image_2"] = _file_size(paths["image_2"])
        except (kio.KittiParseError, OSError) as exc:
            q["errors"].append({"kind": "image_2", "path": config.display_path(paths["image_2"]), "message": str(exc)})

    # 보정
    calib = None
    if paths["calib"]:
        try:
            calib = kio.parse_calib(paths["calib"])
            q["bytes"]["calib"] = _file_size(paths["calib"])
        except (kio.KittiParseError, OSError) as exc:
            q["errors"].append({"kind": "calib", "path": config.display_path(paths["calib"]), "message": str(exc)})

    # 라벨 → objects 행
    objects: list[tuple] = []
    n_objects = None
    if paths["label_2"]:
        try:
            parsed = kio.parse_label(paths["label_2"])
            q["bytes"]["label_2"] = _file_size(paths["label_2"])
        except (kio.KittiParseError, OSError, UnicodeDecodeError) as exc:
            q["errors"].append({"kind": "label_2", "path": config.display_path(paths["label_2"]), "message": str(exc)})
            parsed = None
        if parsed is not None:
            q["label_parsed"] = True
            n_objects = len(parsed)
            for o in parsed:
                valid = geo.is_valid_3d(o["class"], o["h"], o["w"], o["l"], o["z"])
                if o["is_dontcare"]:
                    q["n_dontcare"] += 1
                else:
                    reasons = geo.object_range_errors(o, image_w, image_h)
                    if reasons:
                        q["object_errors"].append((o["object_id"], reasons))
                    if not valid:
                        q["invalid_3d"].append(o["object_id"])
                objects.append(
                    (frame_id, o["object_id"], o["class"])
                    + tuple(o[k] for k in kio.LABEL_FIELDS[1:])
                    + (o["is_dontcare"], valid)
                )
    q["n_objects"] = n_objects or 0

    # LiDAR
    n_points = None
    velo_bytes = None
    if paths["velodyne"]:
        try:
            velo_bytes = _file_size(paths["velodyne"])
            pts = kio.read_velodyne(paths["velodyne"])
        except (kio.KittiParseError, OSError) as exc:
            q["errors"].append({"kind": "velodyne", "path": config.display_path(paths["velodyne"]), "message": str(exc)})
            pts = None
        if pts is not None:
            n_points = int(pts.shape[0])
            q["bytes"]["velodyne"] = velo_bytes
            q["n_points"] = n_points
            masks = geo.point_range_masks(pts)
            for r in _POINT_REASONS:
                q["point_reasons"][r] = int(np.count_nonzero(masks[r]))
            q["n_points_with_error"] = int(np.count_nonzero(masks["any"]))
            if writer_cb is not None:
                writer_cb(frame_id, pts)
            del pts, masks

    frame_row = (
        frame_id,
        paths["image_2"],
        paths["velodyne"],
        paths["label_2"],
        paths["calib"],
        paths["image_2"] is not None,
        paths["velodyne"] is not None,
        paths["label_2"] is not None,
        paths["calib"] is not None,
        image_w,
        image_h,
        n_points,
        velo_bytes,
        n_objects,
    ) + tuple(calib[k].ravel().tolist() if calib is not None else None for k in _CALIB_COLS)
    return {"frame": frame_row, "objects": objects, "quality": q}


def _points_table(frame_id: int, pts: np.ndarray) -> pa.Table:
    n = pts.shape[0]
    cols = [
        pa.array(np.full(n, frame_id, dtype=np.int32)),
        pa.array(np.arange(n, dtype=np.int32)),
    ] + [pa.array(np.ascontiguousarray(pts[:, i])) for i in range(4)]
    return pa.Table.from_arrays(cols, schema=POINTS_ARROW_SCHEMA)


def _open_points_writer(path: Path) -> pq.ParquetWriter:
    return pq.ParquetWriter(
        str(path),
        POINTS_ARROW_SCHEMA,
        compression=POINTS_COMPRESSION,
        use_dictionary=["frame_id"],
        column_encoding={
            "point_id": "DELTA_BINARY_PACKED",
            "x": "BYTE_STREAM_SPLIT",
            "y": "BYTE_STREAM_SPLIT",
            "z": "BYTE_STREAM_SPLIT",
            "reflectance": "BYTE_STREAM_SPLIT",
        },
    )


def process_partition(
    part: int, items: Iterable[list[tuple[int, dict[str, str | None]]]], points_dir: str
) -> Iterator[dict[str, Any]]:
    """파티션 하나(연속 프레임 구간)를 처리한다. Spark mapPartitionsWithIndex 함수.

    프레임을 오름차순으로 처리하며 points 파일 하나에 프레임마다 row group 하나를 쓴다.
    프레임별 결과 dict를 하나씩 내보내고, 마지막에 파일 정보 dict({"points_file": ...})를 내보낸다.
    """
    chunk = sorted((fid, paths) for batch in items for fid, paths in batch)
    if not chunk:
        return
    first, last = chunk[0][0], chunk[-1][0]
    out_path = Path(points_dir) / points_file_name(part, first, last)
    writer: pq.ParquetWriter | None = None
    written: list[int] = []
    n_rows = 0

    def write(fid: int, pts: np.ndarray) -> None:
        nonlocal writer, n_rows
        if pts.shape[0] == 0:
            return  # 빈 .bin은 row group을 만들지 않는다
        if writer is None:
            writer = _open_points_writer(out_path)
        writer.write_table(_points_table(fid, pts), row_group_size=pts.shape[0])
        written.append(fid)
        n_rows += pts.shape[0]

    t0 = time.perf_counter()
    try:
        for fid, paths in chunk:
            yield process_frame(fid, paths, write)
    finally:
        if writer is not None:
            writer.close()
    if writer is not None:
        yield {
            "points_file": {
                "file": out_path.name,
                "part": part,
                "frame_min": first,
                "frame_max": last,
                "frames": written,
                "n_rows": n_rows,
                "n_row_groups": len(written),
                "bytes": out_path.stat().st_size,
                "task_seconds": round(time.perf_counter() - t0, 3),
            }
        }


# --------------------------------------------------------------------------
# 품질 보고서 집계
# --------------------------------------------------------------------------
def aggregate_quality(index_report: dict[str, Any], qs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """프레임별 품질 정보를 하나의 보고서 dict로 합친다."""
    errors: dict[str, list[dict[str, Any]]] = {k: [] for k in KINDS}
    for q in qs:
        for e in q["errors"]:
            errors[e["kind"]].append({"frame_id": q["frame_id"], "path": e["path"], "message": e["message"]})
    reason_counts: Counter[str] = Counter()
    obj_err_items: list[dict[str, Any]] = []
    n_obj_err = 0
    invalid_3d: list[list[int]] = []
    point_reasons: Counter[str] = Counter({r: 0 for r in _POINT_REASONS})
    point_err_frames: list[int] = []
    for q in qs:
        for oid, reasons in q["object_errors"]:
            n_obj_err += 1
            reason_counts.update(reasons)
            if len(obj_err_items) < MAX_ITEMS:
                obj_err_items.append({"frame_id": q["frame_id"], "object_id": oid, "reasons": reasons})
        invalid_3d.extend([q["frame_id"], oid] for oid in q["invalid_3d"])
        point_reasons.update(q["point_reasons"])
        if q["n_points_with_error"]:
            point_err_frames.append(q["frame_id"])

    bytes_per_kind = {k: int(sum(q["bytes"][k] for q in qs)) for k in KINDS}
    total_points = int(sum(q["n_points"] for q in qs))
    return {
        "index": index_report,
        "labels": {
            "n_files_parsed": sum(1 for q in qs if q["label_parsed"]),
            "parse_errors": {"count": len(errors["label_2"]), "items": errors["label_2"]},
            "n_objects_total": int(sum(q["n_objects"] for q in qs)),
            "dontcare": {
                "count": int(sum(q["n_dontcare"] for q in qs)),
                "note": "DontCare는 무시 영역 표식이라 3D 값이 원래 무효다. 일반 객체 오류와 따로 세며 "
                "objects에 is_dontcare=true, valid_3d=false로 보존한다.",
            },
            "object_errors": {
                "n_objects_with_error": n_obj_err,
                "by_reason": dict(sorted(reason_counts.items())),
                "items": obj_err_items,
                "note": "DontCare 제외 일반 객체만. 한 객체가 여러 사유를 가질 수 있다.",
            },
            "invalid_3d": {
                "count": len(invalid_3d),
                "ids": invalid_3d,
                "note": "DontCare 제외. h,w,l > 0 이고 z > 0 이 아닌 객체 [frame_id, object_id].",
            },
        },
        "calib": {"parse_errors": {"count": len(errors["calib"]), "items": errors["calib"]}},
        "image": {"header_errors": {"count": len(errors["image_2"]), "items": errors["image_2"]}},
        "points": {
            "bad_bin_size": {"count": len(errors["velodyne"]), "items": errors["velodyne"]},
            "by_reason": dict(point_reasons),
            "n_points_with_error": int(sum(q["n_points_with_error"] for q in qs)),
            "frames_with_error": {"count": len(point_err_frames), "ids": point_err_frames[:MAX_ITEMS]},
            "policy": "비정상 포인트도 points Parquet에 그대로 쓴다(point_id = 원본 레코드 순서). "
            "보고만 하고 지우지 않는다.",
            "criteria": f"non_finite: x,y,z,reflectance NaN/Inf; coord_range: |x|,|y|,|z| > "
            f"{config.POINT_ABS_MAX} m; reflectance_range: [0,1] 밖",
        },
        "d3_processed": {
            "n_frames_processed": len(qs),
            "bytes": bytes_per_kind,
            "bytes_total": int(sum(bytes_per_kind.values())),
            "total_points": total_points,
            "note": "실제로 읽은 파일의 크기 합. image_2는 PNG 헤더만 읽지만 연결·처리한 파일 크기를 센다. "
            "읽기에 실패한 파일(크기 오류 .bin, 파싱 오류 라벨 등)은 제외한다.",
        },
    }


# --------------------------------------------------------------------------
# 사람이 읽는 보고서
# --------------------------------------------------------------------------
def _ids_text(ids: Sequence[Any], limit: int = 30) -> str:
    if not ids:
        return "-"
    head = ", ".join(str(i) for i in ids[:limit])
    return head + (f" … (외 {len(ids) - limit}건)" if len(ids) > limit else "")


def _gib(n: int) -> str:
    return f"{n:,} B ({n / 2**30:.2f} GiB)"


def render_markdown(rep: dict[str, Any]) -> str:
    """quality_report.json 내용을 짧은 Markdown 요약으로 만든다."""
    idx, lab, pts, d3 = rep["index"], rep["labels"], rep["points"], rep["d3_processed"]
    lines = [
        "# 품질 검사 보고서 (F-1, F-2, D-3)",
        "",
        f"- 생성 시각(UTC): {rep['generated_at']}",
        f"- 원본: `{rep['root']}`  /  작업 폴더: `{rep['work']}`",
        "- 데이터 출처: KITTI Vision Benchmark Suite (CC BY-NC-SA 3.0)",
        "",
        "## 1. 장면 연결 (F-1)",
        "",
        f"- 대상 장면 {idx['n_frames']:,}개 중 4종 파일이 모두 연결된 장면 {idx['n_linked']:,}개, "
        f"연결되지 않은 장면 {idx['unlinked']['count']:,}개",
        "",
        "| 종류 | 파일 수 | 누락 수 | 누락 frame_id |",
        "| --- | ---: | ---: | --- |",
    ]
    for k in KINDS:
        m = idx["missing"][k]
        lines.append(f"| {k} | {idx['files_per_kind'][k]:,} | {m['count']:,} | {_ids_text(m['ids'])} |")
    lines += [
        "",
        f"- 중복 장면(같은 frame_id로 해석되는 파일이 둘 이상): {idx['duplicates']['count']}개 "
        f"({_ids_text(idx['duplicates']['ids'])})",
        f"- 장면 파일로 인식하지 않은 파일: {idx['ignored_files']['count']}개",
        "",
        "## 2. 라벨·보정·이미지 (F-2)",
        "",
        "| 항목 | 건수 |",
        "| --- | ---: |",
        f"| 객체 행 전체(DontCare 포함) | {lab['n_objects_total']:,} |",
        f"| DontCare (오류와 별도) | {lab['dontcare']['count']:,} |",
        f"| 범위 오류가 있는 일반 객체 | {lab['object_errors']['n_objects_with_error']:,} |",
    ]
    for reason, n in lab["object_errors"]["by_reason"].items():
        lines.append(f"| └ {reason} | {n:,} |")
    lines += [
        f"| 3D 박스 무효(일반 객체) | {lab['invalid_3d']['count']:,} |",
        f"| 라벨 파싱 오류 파일 | {lab['parse_errors']['count']:,} |",
        f"| 보정 파싱 오류 파일 | {rep['calib']['parse_errors']['count']:,} |",
        f"| PNG 헤더 오류 파일 | {rep['image']['header_errors']['count']:,} |",
        "",
    ]
    for e in lab["parse_errors"]["items"][:20]:
        lines.append(f"- 라벨 오류 frame {e['frame_id']}: `{e['message']}`")
    lines += [
        "",
        "## 3. LiDAR 포인트 (F-2)",
        "",
        "| 항목 | 건수 |",
        "| --- | ---: |",
        f"| 크기가 16바이트 배수가 아닌 .bin | {pts['bad_bin_size']['count']:,} |",
        f"| 비정상 포인트(사유 하나 이상) | {pts['n_points_with_error']:,} |",
    ]
    for reason, n in pts["by_reason"].items():
        lines.append(f"| └ {reason} | {n:,} |")
    lines += [
        "",
        f"- 기준: {pts['criteria']}",
        f"- 정책: {pts['policy']}",
        "",
        "## 4. 실제 처리 용량 (D-3)",
        "",
        f"- 처리한 장면 수: {d3['n_frames_processed']:,}",
        f"- 총 포인트 수: {d3['total_points']:,}",
        "",
        "| 종류 | 처리 용량 |",
        "| --- | ---: |",
    ]
    for k in KINDS:
        lines.append(f"| {k} | {_gib(d3['bytes'][k])} |")
    lines.append(f"| 합계 | {_gib(d3['bytes_total'])} |")
    if "parquet" in rep:
        pqr = rep["parquet"]
        lines += [
            "",
            "## 5. Parquet 출력 (F-3)",
            "",
            "| 테이블 | 행 수 | 파일 수 | 크기 |",
            "| --- | ---: | ---: | ---: |",
        ]
        for t in TABLES:
            lines.append(
                f"| {t} | {pqr[t]['rows']:,} | {pqr[t]['files']:,} | {_gib(pqr[t]['bytes'])} |"
            )
        lines.append(f"\n- points 압축: {pqr['points']['compression']}")
    if "timings_s" in rep:
        lines += ["", "## 6. 단계별 시간", "", "| 단계 | 초 |", "| --- | ---: |"]
        for name, sec in rep["timings_s"].items():
            lines.append(f"| {name} | {sec:.2f} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 출력 교체·manifest
# --------------------------------------------------------------------------
def _dir_stats(path: Path, suffix: str = ".parquet") -> tuple[int, int]:
    files = [p for p in path.rglob(f"*{suffix}")]
    return len(files), int(sum(p.stat().st_size for p in files))


def _invalidate_derived(parquet_dir: Path) -> list[str]:
    """교체한 Parquet 루트의 파생 표를 지우고(낡은 입력 기반) 지운 이름을 돌려준다."""
    removed = []
    for name in DERIVED_TABLES:
        path = parquet_dir / name
        if path.exists():
            shutil.rmtree(path)
            removed.append(name)
            log.warning("removed derived table %s (stale after ingest swap)", path)
    return removed


def completeness(index_report: dict[str, Any], frames_map: dict[int, dict[str, str | None]],
                 frame_ids: Iterable[int] | None) -> dict[str, Any]:
    """대상 장면 대비 연결 상태. expected = 요청 ID(없으면 label_2가 있는 모든 ID)."""
    if frame_ids is not None:
        expected = sorted({int(f) for f in frame_ids})
    else:
        expected = sorted(fid for fid, row in frames_map.items() if row["label_2"] is not None)
    linked = [f for f in expected if f in frames_map and all(frames_map[f][k] for k in KINDS)]
    missing_any = {k: v["count"] for k, v in index_report["missing"].items() if v["count"]}
    return {
        "n_expected": len(expected),
        "n_linked": len(linked),
        "missing_by_kind": missing_any,
        "complete": len(linked) == len(expected) and not missing_any,
    }


def _swap_into_place(staging: Path, parquet_dir: Path, run_id: str) -> None:
    """staging/<table>을 parquet_dir/<table>로 바꿔 넣는다(기존 폴더는 옮긴 뒤 지움)."""
    old = parquet_dir / f".old-{run_id}"
    for table in TABLES:
        target = parquet_dir / table
        if target.exists():
            old.mkdir(exist_ok=True)
            os.replace(target, old / table)
        os.replace(staging / table, target)
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)


def _cleanup_stale(parquet_dir: Path) -> None:
    for p in parquet_dir.glob(".staging-*"):
        shutil.rmtree(p, ignore_errors=True)
    for p in parquet_dir.glob(".old-*"):
        shutil.rmtree(p, ignore_errors=True)


def load_manifest(work: Path | str | None = None) -> dict[str, Any]:
    """KITTI_WORK/parquet/points/_manifest.json을 읽는다."""
    base = Path(work).expanduser() if work is not None else config.KITTI_WORK
    path = base / "parquet" / "points" / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def points_files_for(frame_ids: Iterable[int], work: Path | str | None = None) -> list[str]:
    """주어진 장면의 포인트가 들어 있는 points 파일 경로 목록(정렬)을 manifest로 고른다.

    전체 폴더를 스캔하지 않고 spark.read.parquet(*files)에 바로 넘길 수 있다.
    포인트가 없는 장면(누락·크기 오류 .bin)은 어느 파일에도 없으므로 무시된다.
    """
    base = Path(work).expanduser() if work is not None else config.KITTI_WORK
    wanted = {int(f) for f in frame_ids}
    manifest = load_manifest(base)
    points_dir = base / "parquet" / "points"
    return sorted(
        str(points_dir / f["file"]) for f in manifest["files"] if wanted.intersection(f["frames"])
    )


def read_frames_file(path: Path | str) -> list[int]:
    """장면 목록 파일(한 줄에 frame_id 하나, `#` 주석·빈 줄 허용)을 읽는다."""
    ids: list[int] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            ids.append(int(s))
    return ids


# --------------------------------------------------------------------------
# 전체 실행
# --------------------------------------------------------------------------
def run(
    root: Path | str | None = None,
    work: Path | str | None = None,
    frame_ids: Iterable[int] | None = None,
    master: str = "local[8]",
    n_partitions: int = config.N_PARTITIONS,
    results_dir: Path | str | None = None,
    spark: Any = None,
    *,
    out_root: Path | str | None = None,
    force_replace: bool = False,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    """F-1~F-4 수집·정제를 실행하고 품질 보고서 dict를 돌려준다.

    출력: <out_root>/{frames,objects,points} (기본 <work>/parquet),
    <results_dir>/quality_report.{json,md}.

    Raises:
        IngestRefused: 부분집합이 정본(<work>/parquet 또는 기본 results/)에 쓰려 할 때(force_replace
            없이), 또는 대상 장면 연결이 불완전할 때(allow_incomplete 없이). 둘 다 쓰기 전에 검사한다.
    """
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    root = Path(root if root is not None else config.KITTI_ROOT).expanduser().resolve()
    work = Path(work if work is not None else config.KITTI_WORK).expanduser().resolve()
    results = Path(results_dir if results_dir is not None else RESULTS_DIR).expanduser()
    canonical = work / "parquet"
    parquet_dir = Path(out_root).expanduser().resolve() if out_root is not None else canonical
    requested = sorted({int(f) for f in frame_ids}) if frame_ids is not None else None
    if requested is not None and not force_replace:
        if parquet_dir == canonical:
            raise IngestRefused(
                f"frame subset ({len(requested)} frames) would replace canonical tables in {canonical}; "
                "pass --out-parquet DIR (out_root) or --force-replace"
            )
        if results.resolve() == RESULTS_DIR.resolve():
            raise IngestRefused(
                f"frame subset would overwrite canonical {RESULTS_DIR}/quality_report.*; "
                "pass --results DIR or --force-replace"
            )

    with _phase("index", timings):
        idx = build_index(root, requested)
        frames_map = idx["frames"]
        log.info(
            "frames=%d linked=%d unlinked=%d duplicates=%d",
            idx["report"]["n_frames"], idx["report"]["n_linked"],
            idx["report"]["unlinked"]["count"], idx["report"]["duplicates"]["count"],
        )
    comp = completeness(idx["report"], frames_map, requested)
    if not comp["complete"]:
        msg = (f"incomplete input: linked {comp['n_linked']} of {comp['n_expected']} expected frames, "
               f"missing files by kind {comp['missing_by_kind']}")
        if not allow_incomplete:
            raise IngestRefused(msg + "; pass --allow-incomplete to ingest anyway")
        log.warning("%s (allowed by --allow-incomplete)", msg)
    frame_set = {**config.frame_set_info(frames_map), "ids": list(frames_map)}

    parquet_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale(parquet_dir)
    run_id = uuid.uuid4().hex[:12]
    staging = parquet_dir / f".staging-{run_id}"
    staging_points = staging / "points"
    staging_points.mkdir(parents=True)

    with _phase("spark_start", timings):
        if spark is None:
            # Spark Python worker가 kitti 패키지를 import할 수 있게 한다(JVM 시작 전에 설정).
            pp = os.environ.get("PYTHONPATH", "")
            if str(REPO_DIR) not in pp.split(os.pathsep):
                os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO_DIR), pp) if p)
            config.KITTI_WORK = work  # spark.local.dir = <work>/spark-tmp
            spark = config.get_spark(master, app="kitti-ingest")
        from . import schema  # pyspark 의존: 드라이버에서만 import

    with _phase("process_frames", timings):
        chunks = contiguous_chunks(list(frames_map), n_partitions)
        payload = [[(fid, frames_map[fid]) for fid in c] for c in chunks]
        points_dir = str(staging_points)
        outputs = (
            spark.sparkContext.parallelize(payload, max(len(payload), 1))
            .mapPartitionsWithIndex(lambda i, it: process_partition(i, it, points_dir))
            .collect()
        )
        frame_results = [o for o in outputs if "frame" in o]
        file_infos = sorted((o["points_file"] for o in outputs if "points_file" in o), key=lambda f: f["frame_min"])
        frame_results.sort(key=lambda r: r["frame"][0])
        log.info("processed frames=%d points files=%d", len(frame_results), len(file_infos))

    with _phase("write_frames", timings):
        frame_rows = [r["frame"] for r in frame_results]
        (
            spark.createDataFrame(frame_rows, schema.FRAMES)
            .coalesce(1)
            .write.option("compression", POINTS_COMPRESSION)
            .parquet(str(staging / "frames"))
        )

    with _phase("write_objects", timings):
        object_rows = [row for r in frame_results for row in r["objects"]]
        (
            spark.createDataFrame(object_rows, schema.OBJECTS)
            .coalesce(1)
            .write.option("compression", POINTS_COMPRESSION)
            .parquet(str(staging / "objects"))
        )

    with _phase("finalize", timings):
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema": [{"name": f.name, "type": str(f.type)} for f in POINTS_ARROW_SCHEMA],
            "compression": POINTS_COMPRESSION,
            "layout": "파일 하나 = 연속 frame_id 구간(파일명 양 끝 포함), row group 하나 = 프레임 하나, "
            "frame_id·point_id 오름차순",
            "files": [{k: v for k, v in f.items() if k != "task_seconds"} for f in file_infos],
            "frame_set": frame_set,
        }
        _write_json_atomic(staging_points / MANIFEST_NAME, manifest)
        _write_json_atomic(staging / "frames" / MANIFEST_NAME, {
            "created_at": manifest["created_at"],
            "run_id": run_id,
            "root": str(root),
            "frame_set": frame_set,
            "subset": requested is not None,
            "completeness": comp,
            "rows": {"frames": len(frame_rows), "objects": len(object_rows),
                     "points": int(sum(f["n_rows"] for f in file_infos))},
        })
        _swap_into_place(staging, parquet_dir, run_id)
        removed = _invalidate_derived(parquet_dir)

    report = aggregate_quality(idx["report"], [r["quality"] for r in frame_results])
    n_files = {t: _dir_stats(parquet_dir / t) for t in TABLES}
    report["parquet"] = {
        "dir": config.display_path(parquet_dir),
        "removed_derived_tables": removed,
        "frames": {"rows": len(frame_rows), "files": n_files["frames"][0], "bytes": n_files["frames"][1]},
        "objects": {"rows": len(object_rows), "files": n_files["objects"][0], "bytes": n_files["objects"][1]},
        "points": {
            "rows": int(sum(f["n_rows"] for f in file_infos)),
            "files": n_files["points"][0],
            "bytes": n_files["points"][1],
            "compression": POINTS_COMPRESSION,
            "max_task_seconds": max((f["task_seconds"] for f in file_infos), default=0.0),
        },
    }
    timings["total"] = round(time.perf_counter() - t_all, 3)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": config.display_path(root),
        "work": config.display_path(work),
        "master": spark.sparkContext.master,
        "n_partitions": len(chunks),
        "frame_set": {k: v for k, v in frame_set.items() if k != "ids"},
        "completeness": comp,
        **report,
        "timings_s": timings,
    }
    with _phase("write_report", timings):
        _write_json_atomic(results / "quality_report.json", report)
        _write_text_atomic(results / "quality_report.md", render_markdown(report))
    log.info("[phase] total: %.2f s", timings["total"])
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KITTI 수집·정제 (F-1~F-4, D-3)")
    parser.add_argument("--root", default=str(config.KITTI_ROOT), help="KITTI 루트 (training/ 상위)")
    parser.add_argument("--work", default=str(config.KITTI_WORK), help="Parquet 등 산출물 폴더")
    parser.add_argument("--frames-file", help="처리할 frame_id 목록 파일(한 줄에 하나)")
    parser.add_argument("--master", default="local[8]")
    parser.add_argument("--partitions", type=int, default=config.N_PARTITIONS)
    parser.add_argument("--results", default=str(RESULTS_DIR), help="품질 보고서 폴더")
    parser.add_argument("--out-parquet", default=None, help="Parquet 출력 루트 (기본 <work>/parquet)")
    parser.add_argument("--force-replace", action="store_true",
                        help="부분집합으로 정본 <work>/parquet·results/를 교체하는 것을 허용")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="대상 장면의 파일 누락이 있어도 적재(기본은 거부)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    frame_ids = read_frames_file(args.frames_file) if args.frames_file else None
    try:
        rep = run(args.root, args.work, frame_ids, args.master, args.partitions, args.results,
                  out_root=args.out_parquet, force_replace=args.force_replace,
                  allow_incomplete=args.allow_incomplete)
    except IngestRefused as exc:
        log.error("refused: %s", exc)
        return 2
    d3 = rep["d3_processed"]
    log.info(
        "done: frames=%d points=%d bytes=%d -> %s",
        d3["n_frames_processed"], d3["total_points"], d3["bytes_total"], rep["parquet"]["dir"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
