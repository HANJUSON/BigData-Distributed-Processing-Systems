"""F-12 측정 작업의 Spark 실행 (spec Critic 1 방식 A, Critic 3·4). `python -m kitti.bench_job`.

순차 처리(kitti.sequential)와 같은 입력·같은 출력 범위를 Spark로 처리한다.

1. 장면 목록을 frame_id 오름차순으로 정렬해 연속 구간 32개로 나눈다(sc.parallelize, 셔플 없음).
2. **한 번의 mapInArrow**가 프레임마다 원본 label_2·calib·velodyne(.bin)을 한 번만 읽어
   - F-8 object_features를 features.features_from_arrays(순차와 같은 순수 함수)로 계산해
     작업(task)마다 pyarrow로 `<out>/object_features/part-NNNNN_fA-fB.parquet`에 직접 쓰고
     (프레임 하나 = row group 하나, Python Row 경로 없음, spec Critic 2와 같은 방식),
   - 포인트 스트림 (frame_id, x, y, z float32)을 Arrow 배치로 내보낸다.
   .bin을 두 번 읽는 두 번째 mapInArrow는 쓰지 않는다.
3. 포인트 스트림을 `points` 뷰로 등록하고 F-6 격자를 analysis.GRID_SQL(CAST AS DOUBLE,
   NaN·ROI 필터 후 FLOOR, GROUP BY frame_id·grid_x·grid_y)로 집계해 `<out>/grid_stats`에 쓴다.
   이 한 번의 Spark 작업(job) 안에서 2번 mapInArrow가 실행된다.
4. object_features Parquet을 다시 읽어 F-5 클래스별 요약(analysis.F5_CLASS_SQL), F-6 누적 격자,
   F-7 분포(analysis.compute_distributions), F-9 기준값·관측 부족 목록(analysis.compute_thresholds,
   low_observation, low_scenes)을 계산하고 verify.py가 읽는 이름으로 결과 파일을 쓴다.

정상 처리 프레임 규칙은 순차와 같다: .bin을 읽은 프레임. .bin이 없거나 크기 오류인 장면에 객체가 있으면
points_in_box를 모두 NULL로 두고 object_features/_skipped_frames.json에 남긴다(features.velodyne_problem·
write_skipped, 순차와 같은 정책). 작업마다 `<out>/_tasks/part-NNNNN.json`에 맡은 프레임,
처리·미처리 프레임, 작업 안 단계 시간(read/compute/write, 초 합)과 시작·끝 시각을 남긴다.

단계 시간 (F-14, `<out>/timings.json`)
- session: pyspark import + SparkSession(JVM) 시작 + kitti 패키지 배포
- read_compute: 격자 작업 시작 → 마지막 프레임 작업(mapInArrow) 종료 (원본 읽기·F-8·포인트 전달·부분 집계)
- aggregate: 격자 작업의 나머지(최종 GROUP BY·grid_stats 쓰기) + F-5/F-6 누적/F-7/F-9 질의
- write: 결과 CSV·JSON 쓰기
- total: 프로세스 시작(이 모듈 import) → 결과 쓰기 끝. spark.stop()은 포함하지 않는다.

CLI:
    python -m kitti.bench_job --root ~/data/kitti --frames-file samples/sample_100.txt \
        --out DIR --master local[4] [--partitions 32]
"""

from __future__ import annotations

import time

_T_START = time.time()

from . import config  # noqa: E402

# BLAS 스레드 1 고정은 numpy import 전에 해야 효과가 있다(spec Critic 4).
config.set_single_thread_blas()

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TYPE_CHECKING, Any, Iterator, Sequence  # noqa: E402

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from . import analysis  # noqa: E402
from . import features as kf  # noqa: E402
from . import ingest  # noqa: E402
from . import io as kio  # noqa: E402
from . import sequential  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from pyspark.sql import SparkSession

POINTS_DDL: str = "frame_id INT, x FLOAT, y FLOAT, z FLOAT"
TASKS_DIR: str = "_tasks"


# --------------------------------------------------------------------------
# 프레임 작업 (Spark Python worker 안에서 실행)
# --------------------------------------------------------------------------
def _points_batch(fid: int, velo: np.ndarray) -> pa.RecordBatch:
    n = velo.shape[0]
    return pa.RecordBatch.from_arrays(
        [pa.array(np.full(n, fid, dtype=np.int32))]
        + [pa.array(np.ascontiguousarray(velo[:, i])) for i in range(3)],
        names=["frame_id", "x", "y", "z"],
    )


def process_partition(frame_ids: Sequence[int], root: str, out: str, part: int) -> Iterator[pa.RecordBatch]:
    """맡은 프레임을 순서대로 읽어 object_features를 파일로 쓰고 포인트 배치를 내보낸다.

    입력 파일 처리 규칙은 sequential.run과 같다(.bin을 쓸 수 없는 장면은 features.velodyne_problem
    으로 판정해 points_in_box를 NULL로 두고 skipped에 기록, 멈추지 않음).
    """
    t_begin = time.time()
    ids = sorted(int(f) for f in frame_ids)
    timing = {"read": 0.0, "compute": 0.0, "write": 0.0}
    processed: list[int] = []
    unprocessed: list[int] = []
    skipped: list[dict[str, Any]] = []
    n_points = 0
    n_feature_rows = 0
    writer: pq.ParquetWriter | None = None
    feat_path = Path(out) / "object_features" / ingest.points_file_name(part, ids[0], ids[-1]) if ids else None
    try:
        for fid in ids:
            t = time.perf_counter()
            objects = kio.parse_label(kio.frame_path(root, "label_2", fid))
            calib = kio.parse_calib(kio.frame_path(root, "calib", fid)) if objects else None
            problem = kf.velodyne_problem(root, fid)
            velo = kio.read_velodyne(kio.frame_path(root, "velodyne", fid)) if problem is None else None
            if problem is not None and objects:  # sequential과 같은 정책: points_in_box NULL
                skipped.append({"frame_id": fid, "reason": problem})
            timing["read"] += time.perf_counter() - t

            t = time.perf_counter()
            feat = kf.features_from_arrays(fid, objects, calib, velo) if objects else None
            batch = None
            if velo is not None:
                processed.append(fid)
                n_points += int(velo.shape[0])
                batch = _points_batch(fid, velo)
            else:
                unprocessed.append(fid)
            del velo
            timing["compute"] += time.perf_counter() - t

            t = time.perf_counter()
            if feat is not None and feat.num_rows:
                if writer is None:
                    writer = pq.ParquetWriter(feat_path, kf.ARROW_SCHEMA)
                writer.write_table(feat)
                n_feature_rows += feat.num_rows
            timing["write"] += time.perf_counter() - t

            if batch is not None and batch.num_rows:
                yield batch
    finally:
        if writer is not None:
            writer.close()
    info = {
        "part": part,
        "frames": ids,
        "processed": processed,
        "unprocessed": unprocessed,
        "skipped": skipped,
        "n_points": n_points,
        "object_feature_rows": n_feature_rows,
        "timings_s": {k: round(v, 6) for k, v in timing.items()},
        "t_begin": t_begin,
        "t_end": time.time(),
    }
    (Path(out) / TASKS_DIR / f"part-{part:05d}.json").write_text(json.dumps(info), encoding="utf-8")


def _map_partition(root: str, out: str):
    def run(batches: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        from pyspark import TaskContext

        ids: list[int] = []
        for b in batches:
            ids.extend(b.column("frame_id").to_pylist())
        yield from process_partition(ids, root, out, TaskContext.get().partitionId())

    return run


# --------------------------------------------------------------------------
# 드라이버
# --------------------------------------------------------------------------
def _fresh(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def run_job(
    spark: "SparkSession",
    frame_ids: Sequence[int],
    root: Path | str | None,
    out: Path | str,
    n_partitions: int = config.N_PARTITIONS,
) -> dict[str, Any]:
    """측정 작업을 실행해 out에 결과를 쓰고 단계 시간·건수를 돌려준다(timings.json은 쓰지 않는다)."""
    from pyspark.sql import functions as F

    from . import schema

    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    feat_dir = _fresh(out / "object_features")
    _fresh(out / TASKS_DIR)
    grid_dir = out / "grid_stats"
    if grid_dir.exists():
        shutil.rmtree(grid_dir)
    ids = sorted({int(f) for f in frame_ids})
    root_str = str(config.training_dir(root).parent)
    phases: dict[str, float] = {}

    # 1) 프레임 작업 + 격자 집계 (Spark 작업 하나)
    t_job = time.time()
    rdd = spark.sparkContext.parallelize([(f,) for f in ids], n_partitions)
    frames_df = spark.createDataFrame(rdd, "frame_id INT")
    points = frames_df.mapInArrow(_map_partition(root_str, str(out)), POINTS_DDL)
    points.createOrReplaceTempView("points")
    agg = spark.sql(analysis.GRID_SQL).select(
        [F.col(f.name).cast(f.dataType).alias(f.name) for f in schema.GRID_STATS]
    )
    agg.sortWithinPartitions("frame_id", "grid_x", "grid_y").write.mode("overwrite").parquet(str(grid_dir))
    t_job_end = time.time()

    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / TASKS_DIR).glob("part-*.json"))]
    covered = sorted(f for t in tasks for f in t["frames"])
    if covered != ids:
        raise RuntimeError(f"frame tasks covered {len(covered)} frames, expected {len(ids)}")
    t_tasks_end = max((t["t_end"] for t in tasks), default=t_job)
    phases["read_compute"] = max(0.0, t_tasks_end - t_job)
    grid_tail = max(0.0, t_job_end - t_tasks_end)
    processed = sorted(f for t in tasks for f in t["processed"])
    unprocessed = sorted(f for t in tasks for f in t["unprocessed"])
    skipped = sorted((s for t in tasks for s in t["skipped"]), key=lambda s: s["frame_id"])

    # 2) 집계 (F-5, F-6 누적, F-7, F-9)
    t = time.time()
    # F5_CLASS_SQL이 결합하는 frames 뷰: 모든 장면, 미처리 장면은 n_points NULL
    done = set(processed)
    spark.createDataFrame(
        [(f, 0 if f in done else None) for f in ids], "frame_id INT, n_points INT"
    ).createOrReplaceTempView("frames")
    feats = spark.read.schema(schema.OBJECT_FEATURES).parquet(str(feat_dir))
    feats.createOrReplaceTempView("object_features")
    f5_rows = [r.asDict() for r in spark.sql(analysis.F5_CLASS_SQL).orderBy("class").collect()]
    grid = spark.read.schema(schema.GRID_STATS).parquet(str(grid_dir))
    cum = analysis.cumulative_grid(spark, grid, len(processed))
    dist = analysis.compute_distributions(spark)
    thr_df = analysis.compute_thresholds(feats)
    thr_pdf = thr_df.toPandas()
    thr_pdf["_rank"] = thr_pdf["class"].map(analysis._class_rank)
    thr_pdf = thr_pdf.sort_values("_rank").drop(columns="_rank").reset_index(drop=True)
    low_df = analysis.low_observation(feats, thr_df)
    low_pdf = low_df.toPandas()
    scenes_pdf = analysis.low_scenes(low_df).toPandas()
    n_feat_rows = sum(t["object_feature_rows"] for t in tasks)
    phases["aggregate"] = grid_tail + (time.time() - t)

    # 3) 결과 파일
    t = time.time()
    kf.write_skipped(feat_dir, ids, root, skipped)
    analysis._write_json(
        out / "f5_dataframe_vs_sql.json",
        {"source": "kitti.bench_job (analysis.F5_CLASS_SQL only)", "per_class_rows": f5_rows},
    )
    sequential.write_cumulative(cum["sum"], len(processed), out)
    analysis._write_json(
        out / "grid_params.json",
        analysis.grid_params(len(processed), analysis.pick_sample_frames(processed), frame_ids=ids),
    )
    for name, pdf in dist.items():
        pdf.to_csv(out / f"f7_{name}.csv", index=False)
    thr_pdf.to_csv(out / "f9_thresholds.csv", index=False)
    low_pdf.to_csv(out / "f9_low_objects.csv", index=False)
    scenes_pdf.to_csv(out / "f9_low_scenes.csv", index=False)
    phases["write"] = time.time() - t

    task_sum = {k: round(sum(tk["timings_s"][k] for tk in tasks), 6) for k in ("read", "compute", "write")}
    return {
        "phases": phases,
        "grid_job_s": round(t_job_end - t_job, 6),
        "grid_tail_s": round(grid_tail, 6),
        "n_frames": len(ids),
        "n_frames_processed": len(processed),
        "unprocessed_frames": unprocessed,
        "skipped_feature_frames": skipped,
        "frame_set": config.frame_set_info(ids),
        "n_points": sum(t["n_points"] for t in tasks),
        "object_feature_rows": n_feat_rows,
        "n_tasks": len(tasks),
        "task_time_sum_s": task_sum,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F-12 측정 작업 (Spark, 방식 A)")
    parser.add_argument("--root", default=str(config.KITTI_ROOT), help="KITTI 루트 (training/ 상위)")
    parser.add_argument("--frames-file", required=True, help="frame_id 목록 파일")
    parser.add_argument("--out", required=True, help="출력 폴더 (verify.py가 읽는 배치)")
    parser.add_argument("--master", default="local[8]")
    parser.add_argument("--partitions", type=int, default=config.N_PARTITIONS,
                        help="입력·셔플 파티션 수 (F-12 고정값 32)")
    args = parser.parse_args(argv)
    ids = kf.read_frame_ids(args.frames_file)

    t = time.time()
    # 입력 파티션 수와 셔플 파티션 수를 같게 둔다(측정 기본 32, 시험에서는 작게).
    spark = config.get_spark(
        args.master, app="kitti-bench",
        extra_conf={"spark.sql.shuffle.partitions": str(args.partitions),
                    "spark.default.parallelism": str(args.partitions),
                    "spark.ui.showConsoleProgress": "false"},
    )
    kf._ship_package(spark)
    t_session = time.time() - t
    try:
        info = run_job(spark, ids, args.root, args.out, args.partitions)
        total = time.time() - _T_START
        phases = {"session": t_session, **info.pop("phases")}
        timings = {
            "mode": "spark",
            "master": args.master,
            "partitions": args.partitions,
            "conf": {k: spark.conf.get(k) for k in (
                "spark.sql.shuffle.partitions", "spark.sql.adaptive.enabled",
                "spark.driver.memory", "spark.local.dir")},
            "root": str(config.training_dir(args.root).parent),
            **info,
            "timings_s": {k: round(v, 6) for k, v in phases.items()},
            "pre_session_s": round(t - _T_START, 6),
            "total_s": round(total, 6),
            "blas_threads": {v: os.environ.get(v) for v in config.BLAS_THREAD_VARS},
        }
        analysis._write_json(Path(args.out).expanduser() / "timings.json", timings)
    finally:
        spark.stop()
    print(
        f"bench_job master={args.master} frames={timings['n_frames']} "
        f"processed={timings['n_frames_processed']} objects={timings['object_feature_rows']} "
        f"total_s={timings['total_s']:.3f} timings={timings['timings_s']} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
