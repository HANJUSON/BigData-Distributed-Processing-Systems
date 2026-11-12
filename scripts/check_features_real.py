"""실제 KITTI 데이터로 object_features(F-8) 점검: 장면 0..9의 객체별 포인트 수와 Spark==순차 대조.

사용:
    .venv/bin/python scripts/check_features_real.py [--root ~/data/kitti] [--master local[8]]

출력 Parquet은 KITTI_WORK/check_features/{sequential,spark}에 덮어쓴다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitti import config  # noqa: E402

config.set_single_thread_blas()

import pyarrow.parquet as pq  # noqa: E402

from kitti import features as kf  # noqa: E402

FRAME_IDS = list(range(10))


def _sorted(table):
    return table.sort_by([("frame_id", "ascending"), ("object_id", "ascending")])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="KITTI 루트 (기본 KITTI_ROOT)")
    parser.add_argument("--master", default="local[8]")
    args = parser.parse_args()

    out_dir = config.KITTI_WORK / "check_features"
    seq_out, spark_out = out_dir / "sequential", out_dir / "spark"

    print(f"{'frame':>5} {'obj':>3} {'class':<14} {'valid':<5} {'dist_m':>8} {'bin':<6} {'diff':>4} {'points':>7}")
    for fid in FRAME_IDS:
        for r in kf.frame_features(fid, args.root).to_pylist():
            dist = "NULL" if r["dist_m"] is None else f"{r['dist_m']:.2f}"
            pts = "NULL" if r["points_in_box"] is None else str(r["points_in_box"])
            print(
                f"{r['frame_id']:>5} {r['object_id']:>3} {r['class']:<14} {str(r['valid_3d']):<5} "
                f"{dist:>8} {str(r['dist_bin']):<6} {r['diff_level']:>4} {pts:>7}"
            )

    n_seq = kf.compute_object_features_sequential(FRAME_IDS, args.root, seq_out)
    spark = config.get_spark(args.master, app="kitti-check-features")
    try:
        n_spark = kf.compute_object_features(spark, FRAME_IDS, args.root, spark_out)
    finally:
        spark.stop()

    seq_t = _sorted(pq.read_table(seq_out))
    spark_t = _sorted(pq.read_table(spark_out))
    same_schema = [(f.name, f.type) for f in seq_t.schema] == [(f.name, f.type) for f in spark_t.schema]
    same_rows = seq_t.to_pylist() == spark_t.to_pylist()
    ok = n_seq == n_spark and same_schema and same_rows
    print(f"rows sequential={n_seq} spark={n_spark} schema_equal={same_schema} rows_equal={same_rows}")
    print("Spark == sequential: PASS" if ok else "Spark == sequential: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
