"""Python 순차 처리 기준 구현 (README F-11, 7.5). pyspark를 import하지 않는다.

같은 장면 목록을 **하나의 for 루프**(frame_id 오름차순)로 처리한다. 프레임마다 원본
label_2·calib·velodyne(.bin)을 읽고(Parquet 아님), Spark 경로와 같은 순수 함수로 계산한다.

- object_features: features.features_from_arrays (= features.frame_features와 같은 계산)
- grid_stats: geometry.grid_cell_counts (float64, 유한값·ROI 필터 후 floor)
- 두 표는 pyarrow ParquetWriter로 프레임마다 row group 하나씩 이어 쓴다. 격자 행을 Python
  목록에 모으지 않고, 누적 히트맵은 80×80 int64 배열 하나에 더한다(메모리 일정).
- 집계는 kitti.analysis와 같은 정의·같은 파일 형식으로 쓴다.
  F-5 클래스별 요약(f5_per_class.json의 per_class_rows), F-6 grid_cumulative.csv·grid_params.json,
  F-7 f7_*.csv(DontCare 제외), F-9 f9_thresholds.csv·f9_low_objects.csv·f9_low_scenes.csv.
- 단계별 시간(read/compute/write)을 timings.json에 남긴다(F-14).

정상 처리 프레임 = .bin을 읽은 프레임(analysis의 frames.n_points IS NOT NULL과 같은 뜻).
.bin이 없거나 크기가 16바이트 배수가 아니면 격자·평균 분모에서 빠진다. 그 프레임에 객체가 있으면
Spark features 경로와 같은 정책(features.velodyne_problem)으로 points_in_box를 모두 NULL로 두고
object_features/_skipped_frames.json에 장면과 사유를 남긴다(멈추지 않음).

출력 폴더 안전장치: out에 parquet/ 또는 analysis_summary.json이 있으면(Spark 작업·결과 폴더로 보임)
덮어쓰지 않고 거부한다. grid_params.json·timings.json에 장면 집합(n, sha256)을 기록한다.

CLI:
    python -m kitti.sequential --root ~/data/kitti --frames-file samples/sample_100.txt --out DIR
"""

from __future__ import annotations

from . import config

# BLAS 스레드 1 고정은 numpy import 전에 해야 효과가 있다(spec Critic 4).
config.set_single_thread_blas()

import argparse  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from decimal import ROUND_HALF_UP, Decimal, localcontext  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Sequence  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.compute as pc  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from . import analysis  # noqa: E402  (pyspark는 함수 안에서만 import)
from . import features as kf  # noqa: E402
from . import geometry as g  # noqa: E402
from . import io as kio  # noqa: E402

# kitti.schema.GRID_STATS와 같은 이름·순서·자료형의 Arrow 스키마
GRID_ARROW_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("frame_id", pa.int32(), nullable=False),
        pa.field("grid_x", pa.int32(), nullable=False),
        pa.field("grid_y", pa.int32(), nullable=False),
        pa.field("n_points", pa.int64(), nullable=False),
        pa.field("z_sum", pa.float64(), nullable=False),
        pa.field("z_min", pa.float64(), nullable=False),
        pa.field("z_max", pa.float64(), nullable=False),
    ]
)
PART_FILE: str = "part-00000.parquet"
F5_FILE: str = "f5_per_class.json"


# --------------------------------------------------------------------------
# F-5 평균 거리: Spark AVG(CAST(dist_m AS DECIMAL(20,12))) → DOUBLE 재현
# --------------------------------------------------------------------------
_Q12 = Decimal("1e-12")
_Q16 = Decimal("1e-16")
_Q20 = Decimal("1e-20")


def spark_decimal_mean(values: Sequence[float]) -> float | None:
    """Spark의 CAST(AVG(CAST(v AS DECIMAL(20,12))) AS DOUBLE)과 같은 값. 빈 입력이면 None.

    - double → DECIMAL(20,12): 최단 십진 표현(Java Double.toString ≈ Python repr)을 HALF_UP 반올림
    - 합은 정확한 십진 합, AVG는 DECIMAL(38,20) 나눗셈(HALF_UP) 후 결과형 DECIMAL(24,16)로 HALF_UP
    - 마지막에 double로 정확히 반올림
    """
    if len(values) == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 80
        total = sum(
            (Decimal(repr(float(v))).quantize(_Q12, rounding=ROUND_HALF_UP) for v in values),
            Decimal(0),
        )
        avg = (total / Decimal(len(values))).quantize(_Q20, rounding=ROUND_HALF_UP)
        avg = avg.quantize(_Q16, rounding=ROUND_HALF_UP)
    return float(avg)


# --------------------------------------------------------------------------
# 집계 (analysis.py와 같은 정의)
# --------------------------------------------------------------------------
def _sorted_pdf(pdf: pd.DataFrame, second: Sequence[str]) -> pd.DataFrame:
    """analysis.compute_distributions와 같은 정렬: 클래스 순서 → class → 두 번째 키."""
    pdf = pdf.copy()
    pdf["_rank"] = pdf["class"].map(analysis._class_rank)
    return pdf.sort_values(["_rank", "class", *second]).drop(columns="_rank").reset_index(drop=True)


def f5_per_class(feats: pa.Table) -> list[dict[str, Any]]:
    """analysis.F5_CLASS_SQL과 같은 클래스별 요약(class 오름차순)."""
    cls = feats.column("class").to_numpy(zero_copy_only=False)
    valid = feats.column("valid_3d").to_numpy(zero_copy_only=False)
    fid = feats.column("frame_id").to_numpy()
    dist = feats.column("dist_m")
    dist_null = pc.is_null(dist).to_numpy(zero_copy_only=False)
    dist_v = dist.to_numpy(zero_copy_only=False)
    pib = feats.column("points_in_box")
    pib_null = pc.is_null(pib).to_numpy(zero_copy_only=False)
    pib_v = pc.fill_null(pib, 0).to_numpy().astype(np.int64)
    rows: list[dict[str, Any]] = []
    for c in sorted(set(cls.tolist())):
        m = cls == c
        dm = m & ~dist_null
        pm = m & ~pib_null
        rows.append(
            {
                "class": c,
                "n_objects": int(m.sum()),
                "n_valid_3d": int((m & valid).sum()),
                "mean_dist_m": spark_decimal_mean(dist_v[dm]),
                "n_scenes": int(np.unique(fid[m]).size),
                "sum_points_in_box": int(pib_v[pm].sum()) if pm.any() else None,
            }
        )
    return rows


def _group_counts(base: pa.Table, keys: list[str]) -> pd.DataFrame:
    """class_dist·class_occluded·class_truncated 공통: n_objects, n_valid_3d, sum_points_in_box."""
    t = base.append_column("_valid", pc.cast(base.column("valid_3d"), pa.int64())).append_column(
        "_pib", pc.cast(base.column("points_in_box"), pa.int64())
    )
    agg = t.group_by(keys, use_threads=False).aggregate(
        [([], "count_all"), ("_valid", "sum"), ("_pib", "sum")]
    )
    agg = agg.rename_columns(
        {"count_all": "n_objects", "_valid_sum": "n_valid_3d", "_pib_sum": "sum_points_in_box"}
    )
    return agg.select([*keys, "n_objects", "n_valid_3d", "sum_points_in_box"]).to_pandas()


def trunc_bucket(truncated: np.ndarray) -> np.ndarray:
    """analysis.F7_SQL['class_truncated']의 CASE와 같은 구간 라벨."""
    t = np.asarray(truncated, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        conds = [(t < 0) | np.isnan(t), t == 0, t <= 0.15, t <= 0.30, t <= 0.50]
    return np.select(conds, ["invalid", *analysis.TRUNC_BUCKETS[:4]], default=analysis.TRUNC_BUCKETS[4])


def f7_distributions(feats: pa.Table) -> dict[str, pd.DataFrame]:
    """analysis.compute_distributions와 같은 4개 표(DontCare 제외)."""
    base = feats.filter(pc.invert(feats.column("is_dontcare")))
    out: dict[str, pd.DataFrame] = {}
    out["class_dist"] = _sorted_pdf(_group_counts(base, ["class", "dist_bin"]), ["dist_bin"])
    out["class_occluded"] = _sorted_pdf(_group_counts(base, ["class", "occluded"]), ["occluded"])
    tb = base.append_column(
        "trunc_bucket", pa.array(trunc_bucket(base.column("truncated").to_numpy()), pa.string())
    )
    out["class_truncated"] = _sorted_pdf(_group_counts(tb, ["class", "trunc_bucket"]), ["trunc_bucket"])

    def as_int(col: str) -> pa.Array:
        return pc.cast(base.column(col), pa.int64())

    lvl4 = pc.cast(pc.equal(base.column("diff_level"), 4), pa.int64())
    dt = pa.table(
        {
            "class": base.column("class"),
            "e": as_int("eligible_easy"),
            "m": as_int("eligible_moderate"),
            "h": as_int("eligible_hard"),
            "n4": lvl4,
        }
    )
    agg = dt.group_by(["class"], use_threads=False).aggregate(
        [([], "count_all"), ("e", "sum"), ("m", "sum"), ("h", "sum"), ("n4", "sum")]
    )
    pdf = agg.to_pandas().rename(
        columns={
            "count_all": "n_objects",
            "e_sum": "n_eligible_easy",
            "m_sum": "n_eligible_moderate",
            "h_sum": "n_eligible_hard",
            "n4_sum": "n_not_eligible",
        }
    )[["class", "n_objects", "n_eligible_easy", "n_eligible_moderate", "n_eligible_hard", "n_not_eligible"]]
    out["class_difficulty"] = _sorted_pdf(pdf, [])
    return out


def f9_thresholds(feats: pa.Table) -> pd.DataFrame:
    """클래스별 (class, n, k = ceil(n/10), threshold = 오름차순 k번째 값). analysis.THRESHOLD_SQL과 같다."""
    valid = feats.filter(
        pc.and_(feats.column("valid_3d"), pc.is_valid(feats.column("points_in_box")))
    )
    cls = valid.column("class").to_numpy(zero_copy_only=False)
    pib = valid.column("points_in_box").to_numpy()
    rows = []
    for c in sorted(set(cls.tolist())):
        vals = np.sort(pib[cls == c])
        n = int(vals.size)
        k = (n + 9) // 10
        rows.append({"class": c, "n": n, "k": k, "threshold": int(vals[k - 1])})
    pdf = pd.DataFrame(rows, columns=["class", "n", "k", "threshold"])
    pdf = pdf.astype({"n": "int32", "k": "int32", "threshold": "int32"})
    return _sorted_pdf(pdf, []) if len(pdf) else pdf


def f9_low_objects(feats: pa.Table, thr: pd.DataFrame) -> pa.Table:
    """points_in_box <= 클래스 기준값인 유효 3D 객체(analysis.LOW_COLUMNS, 같은 정렬)."""
    valid = feats.filter(
        pc.and_(feats.column("valid_3d"), pc.is_valid(feats.column("points_in_box")))
    )
    thr_t = pa.table(
        {"class": pa.array(thr["class"].tolist(), pa.string()),
         "threshold": pa.array(thr["threshold"].tolist(), pa.int32())}
    )
    joined = valid.join(thr_t, "class", join_type="inner", use_threads=False)
    low = joined.filter(pc.less_equal(joined.column("points_in_box"), joined.column("threshold")))
    low = low.select(list(analysis.LOW_COLUMNS))
    return low.sort_by(
        [("class", "ascending"), ("points_in_box", "ascending"),
         ("frame_id", "ascending"), ("object_id", "ascending")]
    )


def f9_low_scenes(low: pa.Table) -> pd.DataFrame:
    """관측 부족 객체가 있는 장면: frame_id, n_low_objects, classes(정렬·';' 연결)."""
    fid = low.column("frame_id").to_numpy()
    cls = low.column("class").to_numpy(zero_copy_only=False)
    rows = []
    for f in np.unique(fid):
        m = fid == f
        rows.append(
            {"frame_id": int(f), "n_low_objects": int(m.sum()),
             "classes": ";".join(sorted(set(cls[m].tolist())))}
        )
    pdf = pd.DataFrame(rows, columns=["frame_id", "n_low_objects", "classes"])
    return pdf.astype({"frame_id": "int32", "n_low_objects": "int64"})


def write_cumulative(total: np.ndarray, n_frames: int, results: Path) -> np.ndarray:
    """analysis.run의 grid_cumulative.csv와 같은 열·순서로 쓴다. 평균 배열을 돌려준다."""
    mean = total / n_frames if n_frames > 0 else np.zeros_like(total, dtype=np.float64)
    mean = mean.astype(np.float64)
    gx, gy = np.meshgrid(np.arange(config.GRID_NX), np.arange(config.GRID_NY), indexing="ij")
    pd.DataFrame(
        {
            "grid_x": gx.ravel(),
            "grid_y": gy.ravel(),
            "x_min_m": config.ROI_X_MIN + gx.ravel() * config.GRID_CELL,
            "y_min_m": config.ROI_Y_MIN + gy.ravel() * config.GRID_CELL,
            "n_points_sum": total.ravel(),
            "n_points_mean": mean.ravel(),
        }
    ).to_csv(results / "grid_cumulative.csv", index=False)
    return mean


# --------------------------------------------------------------------------
# 전체 실행
# --------------------------------------------------------------------------
def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def run(frame_ids: Sequence[int], root: Path | str | None, out: Path | str) -> dict[str, Any]:
    """순차 기준 구현을 실행해 out 아래에 모든 결과를 쓰고 timings dict를 돌려준다.

    Raises:
        ValueError: out에 parquet/ 폴더나 analysis_summary.json이 있을 때(Spark 쪽 폴더 보호).
    """
    t_all = time.perf_counter()
    out = Path(out).expanduser()
    for marker in ("parquet", "analysis_summary.json"):
        if (out / marker).exists():
            raise ValueError(
                f"refusing to write sequential outputs into {out}: it contains '{marker}' "
                "(looks like a Spark work/results dir; use a separate --out)"
            )
    out.mkdir(parents=True, exist_ok=True)
    feat_dir = _fresh_dir(out / "object_features")
    grid_dir = _fresh_dir(out / "grid_stats")
    ids = sorted({int(f) for f in frame_ids})
    timing = {"read": 0.0, "compute": 0.0, "write": 0.0}
    total = np.zeros((config.GRID_NX, config.GRID_NY), dtype=np.int64)
    processed: list[int] = []
    unprocessed: list[int] = []
    skipped: list[dict[str, Any]] = []
    feat_tables: list[pa.Table] = []
    n_grid_rows = 0

    with pq.ParquetWriter(feat_dir / PART_FILE, kf.ARROW_SCHEMA) as fw, \
            pq.ParquetWriter(grid_dir / PART_FILE, GRID_ARROW_SCHEMA) as gw:
        for fid in ids:
            # read: 원본 파일
            t = time.perf_counter()
            objects = kio.parse_label(kio.frame_path(root, "label_2", fid))
            calib = kio.parse_calib(kio.frame_path(root, "calib", fid)) if objects else None
            problem = kf.velodyne_problem(root, fid)
            velo = kio.read_velodyne(kio.frame_path(root, "velodyne", fid)) if problem is None else None
            if problem is not None and objects:  # features와 같은 정책: points_in_box NULL
                skipped.append({"frame_id": fid, "reason": problem})
            timing["read"] += time.perf_counter() - t

            # compute: 같은 순수 함수
            t = time.perf_counter()
            feat = kf.features_from_arrays(fid, objects, calib, velo) if objects else kf.ARROW_SCHEMA.empty_table()
            grid = None
            if velo is not None:
                processed.append(fid)
                cells = g.grid_cell_counts(velo)
                np.add.at(total, (cells["grid_x"], cells["grid_y"]), cells["n_points"])
                n = cells["grid_x"].size
                if n:
                    grid = pa.Table.from_arrays(
                        [pa.array(np.full(n, fid, dtype=np.int32))]
                        + [pa.array(cells[k]) for k in GRID_ARROW_SCHEMA.names[1:]],
                        schema=GRID_ARROW_SCHEMA,
                    )
            else:
                unprocessed.append(fid)
            del velo
            timing["compute"] += time.perf_counter() - t

            # write: 프레임 하나 = row group 하나
            t = time.perf_counter()
            if feat.num_rows:
                fw.write_table(feat)
                feat_tables.append(feat)
            if grid is not None:
                gw.write_table(grid)
                n_grid_rows += grid.num_rows
            timing["write"] += time.perf_counter() - t

    # 집계(compute)와 결과 파일(write)
    t = time.perf_counter()
    feats = pa.concat_tables(feat_tables) if feat_tables else kf.ARROW_SCHEMA.empty_table()
    del feat_tables
    f5 = f5_per_class(feats)
    dist = f7_distributions(feats)
    thr = f9_thresholds(feats)
    low = f9_low_objects(feats, thr)
    scenes = f9_low_scenes(low)
    timing["compute"] += time.perf_counter() - t

    t = time.perf_counter()
    kf.write_skipped(feat_dir, ids, root, skipped)
    frame_set = config.frame_set_info(ids)
    analysis._write_json(out / F5_FILE, {"source": "kitti.sequential", "per_class_rows": f5})
    write_cumulative(total, len(processed), out)
    sample = analysis.pick_sample_frames(processed)
    analysis._write_json(out / "grid_params.json", analysis.grid_params(len(processed), sample, frame_ids=ids))
    for name, pdf in dist.items():
        pdf.to_csv(out / f"f7_{name}.csv", index=False)
    thr.to_csv(out / "f9_thresholds.csv", index=False)
    low.to_pandas().to_csv(out / "f9_low_objects.csv", index=False)
    scenes.to_csv(out / "f9_low_scenes.csv", index=False)
    timing["write"] += time.perf_counter() - t

    info = {
        "mode": "sequential",
        "root": str(config.training_dir(root).parent),
        "n_frames": len(ids),
        "n_frames_processed": len(processed),
        "unprocessed_frames": unprocessed,
        "skipped_feature_frames": skipped,
        "frame_set": frame_set,
        "object_feature_rows": int(feats.num_rows),
        "grid_rows": int(n_grid_rows),
        "timings_s": {k: round(v, 6) for k, v in timing.items()},
        "total_s": round(time.perf_counter() - t_all, 6),
        "blas_threads": {v: os.environ.get(v) for v in config.BLAS_THREAD_VARS},
    }
    analysis._write_json(out / "timings.json", info)
    return info


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KITTI 순차 기준 구현 (F-11)")
    parser.add_argument("--root", default=str(config.KITTI_ROOT), help="KITTI 루트 (training/ 상위)")
    parser.add_argument("--frames-file", help="frame_id 목록 파일. 없으면 label_2의 모든 장면")
    parser.add_argument("--out", required=True, help="출력 폴더")
    args = parser.parse_args(argv)
    ids = kf.read_frame_ids(args.frames_file) if args.frames_file else kio.list_frame_ids(args.root)["label_2"]
    try:
        info = run(ids, args.root, args.out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"sequential frames={info['n_frames']} processed={info['n_frames_processed']} "
        f"objects={info['object_feature_rows']} grid_rows={info['grid_rows']} "
        f"total_s={info['total_s']:.3f} timings={info['timings_s']} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
