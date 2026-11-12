"""객체별 특징 object_features 계산 (F-7 거리·난이도 열, F-8 박스 내부 포인트 수).

프레임 하나 단위의 순수 함수 `frame_features`를 Spark(`mapInArrow`)와 순차 처리(for 루프)가
똑같이 호출한다. 이 모듈은 import 시 pyspark를 불러오지 않는다(순차 측정 공정성, spec Critic 1).
Spark 관련 import는 `compute_object_features` 안에서만 한다.

열 정책
- DontCare 포함 label_2의 모든 줄이 한 행이 된다(키 (frame_id, object_id) 완전성).
- dist_m = sqrt(x*x + z*z): DontCare면 NULL(DB는 무효 표식 -1000을 NULL로 적재하므로 거리도 NULL).
  valid_3d=false인 일반 객체(예: z ≤ 0)는 location이 실제 값이므로 계산한다.
- dist_bin: dist_m이 NULL이면 NULL, 아니면 '0-10'/'10-20'/'20-40'/'40+'.
- points_in_box: valid_3d=false(DontCare 포함)면 NULL, 유효하면 0 이상의 정수.
- eligible_*·diff_level: geometry.difficulty (DontCare는 모두 false, diff_level 4).

label_2·calib가 없으면 FileNotFoundError, 형식 오류면 KittiParseError를 낸다.

.bin 정책 (Spark·순차 공통, velodyne_problem)
- velodyne .bin이 없거나 크기가 16바이트 배수가 아니면 작업을 멈추지 않는다. 그 장면의 객체 행은
  모두 만들되 points_in_box는 전부 NULL로 둔다(스키마 변경 없음).
- 건너뛴 장면과 사유는 출력 폴더의 사이드카 `_skipped_frames.json`(SKIPPED_FILE)에 남긴다.
  `_`로 시작하므로 Spark·pyarrow Parquet 읽기에서는 무시된다. ingest 품질 보고서도 같은 장면을 보고한다.

CLI:
    python -m kitti.features --mode spark|sequential [--work DIR] [--out DIR] [--frames-file FILE]
                             [--master local[8]]
    --out이 없으면 실행 시점에 <work>/parquet/object_features를 쓴다(--work 기본 KITTI_WORK).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, Sequence

from . import config

if __name__ == "__main__":
    # CLI 실행 시 BLAS 스레드 1 고정은 numpy import 전에 해야 효과가 있다(spec Critic 4).
    config.set_single_thread_blas()

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import geometry as g
from . import io as kio

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from pyspark.sql import SparkSession

# kitti.schema.OBJECT_FEATURES와 같은 이름·순서·자료형·널 허용의 Arrow 스키마.
# (schema.py는 pyspark를 import하므로 순차 경로용으로 따로 둔다. 일치는 시험으로 확인한다.)
ARROW_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("frame_id", pa.int32(), nullable=False),
        pa.field("object_id", pa.int32(), nullable=False),
        pa.field("class", pa.string(), nullable=False),
        pa.field("truncated", pa.float64(), nullable=False),
        pa.field("occluded", pa.int32(), nullable=False),
        pa.field("alpha", pa.float64(), nullable=False),
        pa.field("bbox_left", pa.float64(), nullable=False),
        pa.field("bbox_top", pa.float64(), nullable=False),
        pa.field("bbox_right", pa.float64(), nullable=False),
        pa.field("bbox_bottom", pa.float64(), nullable=False),
        pa.field("h", pa.float64(), nullable=False),
        pa.field("w", pa.float64(), nullable=False),
        pa.field("l", pa.float64(), nullable=False),
        pa.field("x", pa.float64(), nullable=False),
        pa.field("y", pa.float64(), nullable=False),
        pa.field("z", pa.float64(), nullable=False),
        pa.field("ry", pa.float64(), nullable=False),
        pa.field("is_dontcare", pa.bool_(), nullable=False),
        pa.field("valid_3d", pa.bool_(), nullable=False),
        pa.field("dist_m", pa.float64(), nullable=True),
        pa.field("bbox_height", pa.float64(), nullable=False),
        pa.field("eligible_easy", pa.bool_(), nullable=False),
        pa.field("eligible_moderate", pa.bool_(), nullable=False),
        pa.field("eligible_hard", pa.bool_(), nullable=False),
        pa.field("diff_level", pa.int32(), nullable=False),
        pa.field("points_in_box", pa.int32(), nullable=True),
        pa.field("dist_bin", pa.string(), nullable=True),
    ]
)

SEQUENTIAL_FILE: str = "part-00000.parquet"
SKIPPED_FILE: str = "_skipped_frames.json"
"""points_in_box를 계산하지 못한(.bin 없음·크기 오류) 장면 목록 사이드카."""


def default_out(work: Path | str | None = None) -> Path:
    """기본 출력 폴더 <work>/parquet/object_features. work가 없으면 호출 시점의 config.KITTI_WORK."""
    base = Path(work).expanduser() if work is not None else config.KITTI_WORK
    return base / "parquet" / "object_features"


def velodyne_problem(root: Path | str | None, frame_id: int) -> str | None:
    """장면의 .bin을 points_in_box 계산에 쓸 수 없으면 사유 문자열, 쓸 수 있으면 None.

    kio.read_velodyne이 실패하는 조건(파일 없음, 크기가 16바이트 배수 아님)과 같다.
    """
    path = kio.frame_path(root, "velodyne", int(frame_id))
    try:
        size = os.stat(path).st_size
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable: {exc}"
    if size % kio.VELO_RECORD_BYTES != 0:
        return f"bad_size: {size} bytes not divisible by {kio.VELO_RECORD_BYTES}"
    return None


def write_skipped(
    out_path: Path | str,
    frame_ids: Iterable[int],
    root: Path | str | None,
    skipped: list[dict] | None = None,
) -> list[dict]:
    """대상 장면 중 .bin을 쓸 수 없는 장면을 out_path/_skipped_frames.json에 기록하고 목록을 돌려준다.

    객체가 없는 장면은 영향받는 행이 없으므로 기록하지 않는다(라벨을 읽어 확인).
    skipped([{frame_id, reason}])를 주면 다시 조사하지 않고 그대로 쓴다(순차 처리용).
    """
    if skipped is None:
        skipped = []
        for fid in sorted({int(f) for f in frame_ids}):
            reason = velodyne_problem(root, fid)
            if reason is None:
                continue
            if not kio.parse_label(kio.frame_path(root, "label_2", fid)):
                continue
            skipped.append({"frame_id": fid, "reason": reason})
    out = Path(out_path)
    out.mkdir(parents=True, exist_ok=True)
    doc = {
        "policy": "frames whose velodyne .bin is missing or not a multiple of 16 bytes keep all object "
        "rows with points_in_box = NULL; processing does not abort",
        "count": len(skipped),
        "frames": skipped,
    }
    tmp = out / (SKIPPED_FILE + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out / SKIPPED_FILE)
    return skipped


def frame_features(frame_id: int, root: Path | str | None = None) -> pa.Table:
    """장면 하나의 object_features 행을 Arrow 표(ARROW_SCHEMA)로 계산한다.

    label_2·calib·velodyne을 이 장면 것만 읽는다(메모리는 장면 하나 분량).
    velodyne float32 → float64 변환 후 geometry.velo_to_rect로 rect 좌표를 만든다.
    행 순서는 object_id(원본 줄 번호) 오름차순이다.
    .bin이 없거나 크기 오류면(velodyne_problem) points_in_box를 모두 NULL로 둔다.
    """
    fid = int(frame_id)
    objects = kio.parse_label(kio.frame_path(root, "label_2", fid))
    if not objects:
        return ARROW_SCHEMA.empty_table()
    calib = kio.parse_calib(kio.frame_path(root, "calib", fid))
    velo = None
    if velodyne_problem(root, fid) is None:
        velo = kio.read_velodyne(kio.frame_path(root, "velodyne", fid))
    return features_from_arrays(fid, objects, calib, velo)


def features_from_arrays(
    frame_id: int,
    objects: Sequence[dict],
    calib: dict[str, np.ndarray],
    velo: np.ndarray | None,
) -> pa.Table:
    """이미 읽은 라벨·보정·포인트로 frame_features와 같은 표를 만든다(파일 읽기 없음).

    순차 처리(kitti.sequential)가 .bin을 한 번만 읽어 격자 집계와 함께 쓰도록 분리했다.
    frame_features는 파일을 읽은 뒤 이 함수를 그대로 호출한다.
    velo가 None이면(.bin 없음·크기 오류) points_in_box는 모든 객체에서 NULL이다.
    """
    fid = int(frame_id)
    if not objects:
        return ARROW_SCHEMA.empty_table()
    if velo is None:
        counts: list[int | None] = [None] * len(objects)
    else:
        points_rect = g.velo_to_rect(np.asarray(velo)[:, :3].astype(np.float64), calib)
        counts = g.points_in_box_counts(points_rect, objects)
    cols: dict[str, list] = {name: [] for name in ARROW_SCHEMA.names}
    for obj, count in zip(objects, counts):
        is_dc = bool(obj["is_dontcare"])
        valid = g.is_valid_3d(obj["class"], obj["h"], obj["w"], obj["l"], obj["z"])
        bh = g.bbox_height(obj["bbox_top"], obj["bbox_bottom"])
        dist = None if is_dc else g.distance_m(obj["x"], obj["z"])
        diff = g.difficulty(bh, obj["occluded"], obj["truncated"], is_dc)

        cols["frame_id"].append(fid)
        for name in kio.LABEL_FIELDS:
            cols[name].append(obj[name])
        cols["object_id"].append(int(obj["object_id"]))
        cols["is_dontcare"].append(is_dc)
        cols["valid_3d"].append(valid)
        cols["dist_m"].append(dist)
        cols["bbox_height"].append(bh)
        for key in ("eligible_easy", "eligible_moderate", "eligible_hard", "diff_level"):
            cols[key].append(diff[key])
        cols["points_in_box"].append(count)
        cols["dist_bin"].append(None if dist is None else g.dist_bin(dist))
    return pa.Table.from_pydict(cols, schema=ARROW_SCHEMA)


def _arrow_batches(frame_ids: Iterable[int], root: str) -> Iterator[pa.RecordBatch]:
    for fid in frame_ids:
        yield from frame_features(fid, root).to_batches()


def _map_frames(root: str):
    """mapInArrow 함수: 입력 배치의 frame_id마다 frame_features 결과 배치를 내보낸다."""

    def run(batches: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        for batch in batches:
            yield from _arrow_batches(batch.column("frame_id").to_pylist(), root)

    return run


_SHIPPED_APPS: set[str] = set()


def _ship_package(spark: "SparkSession") -> None:
    """kitti 패키지(.py)를 zip으로 묶어 Spark Python worker에 배포한다(애플리케이션당 1회).

    worker는 드라이버의 작업 디렉터리·sys.path를 물려받지 않으므로, 어디서 실행해도
    mapInArrow 함수가 `kitti`를 import할 수 있게 한다.
    """
    sc = spark.sparkContext
    app_id = sc.applicationId
    if app_id in _SHIPPED_APPS:
        return
    pkg = Path(__file__).resolve().parent
    dest = config.spark_local_dir() / "pyfiles" / app_id
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "kitti.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sorted(pkg.glob("*.py")):
            zf.write(src, f"{pkg.name}/{src.name}")
    sc.addPyFile(str(archive))
    _SHIPPED_APPS.add(app_id)


def compute_object_features(
    spark: "SparkSession",
    frame_ids: Sequence[int],
    root: Path | str | None = None,
    out_path: Path | str | None = None,
    n_partitions: int = config.N_PARTITIONS,
) -> int:
    """Spark로 object_features를 계산해 Parquet으로 덮어쓴다(멱등).

    frame_id를 오름차순 정렬해 n_partitions개의 연속 구간으로 나누고(셔플 없음),
    각 작업이 맡은 장면을 하나씩 frame_features로 처리해 Arrow 배치를 내보낸다(mapInArrow).
    out_path가 없으면 default_out(). 쓴 뒤 _skipped_frames.json 사이드카를 남긴다.

    Returns:
        저장한 행 수.
    """
    from . import schema

    _ship_package(spark)
    root_str = str(config.training_dir(root).parent)
    ids = sorted(int(f) for f in frame_ids)
    rdd = spark.sparkContext.parallelize([(f,) for f in ids], n_partitions)
    frames_df = spark.createDataFrame(rdd, "frame_id INT")
    df = frames_df.mapInArrow(_map_frames(root_str), schema.OBJECT_FEATURES)
    out = Path(out_path) if out_path is not None else default_out()
    df.write.mode("overwrite").parquet(str(out))
    write_skipped(out, ids, root_str)
    return spark.read.parquet(str(out)).count()


def compute_object_features_sequential(
    frame_ids: Sequence[int],
    root: Path | str | None = None,
    out_path: Path | str | None = None,
) -> int:
    """순차 처리(pyspark 없음): 같은 frame_features를 for 루프로 돌려 Parquet으로 덮어쓴다.

    out_path는 디렉터리이며 `part-00000.parquet` 한 파일에 장면별 row group으로 이어 쓴다.
    out_path가 없으면 default_out(). 쓴 뒤 _skipped_frames.json 사이드카를 남긴다.

    Returns:
        저장한 행 수.
    """
    out = Path(out_path) if out_path is not None else default_out()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    total = 0
    with pq.ParquetWriter(out / SEQUENTIAL_FILE, ARROW_SCHEMA) as writer:
        for fid in sorted(int(f) for f in frame_ids):
            table = frame_features(fid, root)
            if table.num_rows:
                writer.write_table(table)
                total += table.num_rows
    write_skipped(out, frame_ids, root)
    return total


def read_frame_ids(path: Path | str) -> list[int]:
    """장면 목록 파일(공백·줄 구분 정수, '#' 뒤는 주석)을 읽어 정렬된 frame_id 목록을 돌려준다."""
    ids: list[int] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        ids.extend(int(tok) for tok in line.split("#", 1)[0].split())
    return sorted(set(ids))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="object_features(F-7·F-8) 계산")
    parser.add_argument("--mode", choices=("spark", "sequential"), required=True)
    parser.add_argument("--frames-file", help="frame_id 목록 파일. 없으면 label_2의 모든 장면")
    parser.add_argument("--root", default=None, help="KITTI 루트 (기본 KITTI_ROOT)")
    parser.add_argument("--work", default=None, help="산출물 폴더 (기본 KITTI_WORK)")
    parser.add_argument("--out", default=None, help="출력 Parquet 디렉터리 (기본 <work>/parquet/object_features)")
    parser.add_argument("--master", default="local[8]")
    parser.add_argument("--partitions", type=int, default=config.N_PARTITIONS)
    args = parser.parse_args(argv)
    if args.work is not None:
        config.KITTI_WORK = Path(args.work).expanduser()  # spark.local.dir = <work>/spark-tmp
    args.out = str(Path(args.out).expanduser() if args.out else default_out(args.work))

    if args.frames_file:
        ids = read_frame_ids(args.frames_file)
    else:
        ids = kio.list_frame_ids(args.root)["label_2"]

    start = time.perf_counter()
    if args.mode == "sequential":
        rows = compute_object_features_sequential(ids, args.root, args.out)
    else:
        spark = config.get_spark(args.master, app="kitti-features")
        try:
            rows = compute_object_features(spark, ids, args.root, args.out, args.partitions)
        finally:
            spark.stop()
    elapsed = time.perf_counter() - start
    print(f"mode={args.mode} frames={len(ids)} rows={rows} out={args.out} seconds={elapsed:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
