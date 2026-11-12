"""Spark 배치 분석 (README F-5, F-6, F-7, F-9): 테이블 결합·격자 집계·조건별 분포·관측 부족 추출.

실행:
    python -m kitti.analysis --work ~/data/kitti/work --results results --figures results/figures
                             [--master local[8]] [--frames-file list.txt --results DIR]
                             [--parquet-out DIR] [--subset-name NAME]

부분집합(--frames-file) 실행 보호 (정본 결과를 덮어쓰지 않음)
- --results가 기본 폴더(저장소 results/)면 거부한다(종료 코드 2).
- grid_stats·low_observation(과 그 장면의 object_features 사본)은 기본적으로
  <work>/subsets/<name>/ 아래에 쓴다(name 기본 = 목록 파일 이름, 함수 호출 시 frames-<n>-<hash>).
  --parquet-out이 정본 <work>/parquet이면 거부한다. 이 폴더는 verify --spark-work로 바로 쓸 수 있다.
- grid_params.json·analysis_summary.json에 장면 집합(n, sha256)을 기록한다.

입력: <work>/parquet/{frames,objects,object_features,points} (kitti.ingest, kitti.features 출력)
출력:
- F-5: results/f5_dataframe_vs_sql.json — 같은 질의를 DataFrame API·spark.sql로 실행해 대조
- F-6: <work>/parquet/grid_stats (kitti.schema.GRID_STATS), results/grid_cumulative.csv,
       results/grid_params.json, figures/f6_grid_cumulative.png, figures/f6_sample_scenes.png
- F-7: results/f7_*.csv, figures/f7_*.png (DontCare 제외)
- F-9: results/f9_thresholds.csv, results/f9_low_objects.csv, results/f9_low_scenes.csv,
       <work>/parquet/low_observation, figures/f9_points_distribution.png
- results/analysis_summary.json — S5 화면의 한 줄 결론용 핵심 수치

계산 규칙 (spec Critic 5·7·9·10)
- 좌표는 CAST(x AS DOUBLE) 후 계산하고, ROI·NaN 필터를 FLOOR보다 먼저 적용한다.
  z가 NaN/±Inf인 점도 뺀다(geometry.grid_cell_counts의 isfinite(z)와 같은 규칙).
- 누적 히트맵 평균 = 격자별 포인트 합 / 정상 처리된 프레임 수(frames.n_points IS NOT NULL), 빈 격자 = 0.
- F-9 기준값 = 클래스별 유효 3D 객체의 points_in_box를 오름차순 정렬한 ceil(n/10)번째 값
  (ROW_NUMBER로 정확히 계산, percentile_approx 사용 안 함). points_in_box <= 기준값이면 관측 부족.
- F-5의 평균 거리는 DECIMAL(20,12)로 합산한다. 부동소수 합은 셔플 병합 순서에 따라 마지막 비트가
  달라질 수 있으므로, 정확한(순서 무관) 십진 합으로 두 방식이 비트 단위로 같게 한다.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from . import config
from . import ingest

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from pyspark.sql import DataFrame, SparkSession

log = logging.getLogger("kitti.analysis")

RESULTS_DIR: Path = ingest.RESULTS_DIR
FIGURES_DIR: Path = RESULTS_DIR / "figures"
SAMPLE_FRAMES: tuple[int, ...] = (0, 1000, 5000)
"""F-6 표본 장면. 없으면 처리된 장면 중 앞에서부터 채운다."""
SOURCE_CAPTION: str = "Source: KITTI Vision Benchmark Suite, 3D object training set (CC BY-NC-SA 3.0)"

# README 9.6 Okabe-Ito 클래스 색
CLASS_COLORS: dict[str, str] = {
    "Car": "#0072B2",
    "Van": "#56B4E9",
    "Truck": "#E69F00",
    "Pedestrian": "#D55E00",
    "Person_sitting": "#F0E442",
    "Cyclist": "#009E73",
    "Tram": "#CC79A7",
    "Misc": "#8C8C8C",
}
CLASS_ORDER: tuple[str, ...] = tuple(c for c in config.KITTI_CLASSES if c != config.DONTCARE)
TRUNC_BUCKETS: tuple[str, ...] = ("0", "(0,0.15]", "(0.15,0.30]", "(0.30,0.50]", ">0.50")
MEAN_DECIMAL: str = "DECIMAL(20,12)"


# --------------------------------------------------------------------------
# 입력
# --------------------------------------------------------------------------
def _parquet_dir(work: Path | str) -> Path:
    return Path(work).expanduser() / "parquet"


def _filter_ids(df: "DataFrame", frame_ids: Sequence[int] | None) -> "DataFrame":
    from pyspark.sql import functions as F

    return df if frame_ids is None else df.where(F.col("frame_id").isin(list(frame_ids)))


def read_points(spark: "SparkSession", work: Path | str, frame_ids: Sequence[int] | None = None) -> "DataFrame":
    """points Parquet을 읽는다. frame_ids가 있으면 manifest로 해당 파일만 읽고 frame_id로 거른다."""
    from . import schema

    pdir = _parquet_dir(work) / "points"
    if frame_ids is None:
        return spark.read.schema(schema.POINTS).parquet(str(pdir))
    files = ingest.points_files_for(frame_ids, work)
    if not files:
        return spark.createDataFrame([], schema.POINTS)
    return _filter_ids(spark.read.schema(schema.POINTS).parquet(*files), frame_ids)


def register_views(
    spark: "SparkSession", work: Path | str, frame_ids: Sequence[int] | None = None
) -> dict[str, "DataFrame"]:
    """frames, objects, object_features, points 임시 뷰를 등록하고 DataFrame을 돌려준다."""
    from . import schema

    pq_dir = _parquet_dir(work)
    dfs = {
        name: _filter_ids(spark.read.schema(schema.ALL_SCHEMAS[name]).parquet(str(pq_dir / name)), frame_ids)
        for name in ("frames", "objects", "object_features")
    }
    dfs["points"] = read_points(spark, work, frame_ids)
    for name, df in dfs.items():
        df.createOrReplaceTempView(name)
    return dfs


# --------------------------------------------------------------------------
# F-5 DataFrame ↔ SQL 대조
# --------------------------------------------------------------------------
F5_CLASS_SQL = f"""
SELECT o.class                                              AS class,
       COUNT(*)                                             AS n_objects,
       COUNT(CASE WHEN o.valid_3d THEN 1 END)               AS n_valid_3d,
       CAST(AVG(CAST(o.dist_m AS {MEAN_DECIMAL})) AS DOUBLE) AS mean_dist_m,
       COUNT(DISTINCT o.frame_id)                           AS n_scenes,
       SUM(CAST(o.points_in_box AS BIGINT))                 AS sum_points_in_box
FROM object_features o
JOIN frames f ON o.frame_id = f.frame_id
GROUP BY o.class
"""

F5_SCENE_SQL = """
SELECT f.frame_id                                           AS frame_id,
       f.n_points                                           AS n_points,
       COUNT(CASE WHEN o.is_dontcare = false THEN 1 END)    AS n_objects,
       COUNT(CASE WHEN o.valid_3d THEN 1 END)               AS n_valid_3d,
       COUNT(CASE WHEN o.is_dontcare THEN 1 END)            AS n_dontcare
FROM frames f
LEFT JOIN object_features o ON f.frame_id = o.frame_id
GROUP BY f.frame_id, f.n_points
"""


def f5_class_dataframe(frames: "DataFrame", feats: "DataFrame") -> "DataFrame":
    """F5_CLASS_SQL과 같은 질의를 DataFrame API로 작성한 것."""
    from pyspark.sql import functions as F

    o = feats.alias("o")
    f = frames.alias("f")
    return (
        o.join(f, F.col("o.frame_id") == F.col("f.frame_id"), "inner")
        .groupBy(F.col("o.class").alias("class"))
        .agg(
            F.count(F.lit(1)).alias("n_objects"),
            F.count(F.when(F.col("o.valid_3d"), F.lit(1))).alias("n_valid_3d"),
            F.avg(F.col("o.dist_m").cast(MEAN_DECIMAL.lower())).cast("double").alias("mean_dist_m"),
            F.countDistinct(F.col("o.frame_id")).alias("n_scenes"),
            F.sum(F.col("o.points_in_box").cast("bigint")).alias("sum_points_in_box"),
        )
    )


def f5_scene_dataframe(frames: "DataFrame", feats: "DataFrame") -> "DataFrame":
    """F5_SCENE_SQL과 같은 질의를 DataFrame API로 작성한 것."""
    from pyspark.sql import functions as F

    o = feats.alias("o")
    f = frames.alias("f")
    return (
        f.join(o, F.col("f.frame_id") == F.col("o.frame_id"), "left")
        .groupBy(F.col("f.frame_id").alias("frame_id"), F.col("f.n_points").alias("n_points"))
        .agg(
            F.count(F.when(F.col("o.is_dontcare") == F.lit(False), F.lit(1))).alias("n_objects"),
            F.count(F.when(F.col("o.valid_3d"), F.lit(1))).alias("n_valid_3d"),
            F.count(F.when(F.col("o.is_dontcare"), F.lit(1))).alias("n_dontcare"),
        )
    )


def _compare(df_api: "DataFrame", df_sql: "DataFrame") -> dict[str, Any]:
    n_api, n_sql = df_api.count(), df_sql.count()
    schema_equal = df_api.schema.simpleString() == df_sql.schema.simpleString()
    only_api = df_api.exceptAll(df_sql).count() if schema_equal else None
    only_sql = df_sql.exceptAll(df_api).count() if schema_equal else None
    return {
        "rows_dataframe": n_api,
        "rows_sql": n_sql,
        "schema_dataframe": df_api.schema.simpleString(),
        "schema_sql": df_sql.schema.simpleString(),
        "schema_equal": schema_equal,
        "except_dataframe_minus_sql": only_api,
        "except_sql_minus_dataframe": only_sql,
        "pass": bool(schema_equal and n_api == n_sql and only_api == 0 and only_sql == 0),
    }


def f5_compare(spark: "SparkSession") -> dict[str, Any]:
    """등록된 뷰(frames, object_features)로 두 질의를 DataFrame·SQL 두 방식으로 실행해 대조한다."""
    frames, feats = spark.table("frames"), spark.table("object_features")
    queries = {
        "per_class": (f5_class_dataframe(frames, feats), spark.sql(F5_CLASS_SQL), F5_CLASS_SQL),
        "per_scene": (f5_scene_dataframe(frames, feats), spark.sql(F5_SCENE_SQL), F5_SCENE_SQL),
    }
    out: dict[str, Any] = {"method": "row count equality + exceptAll both directions == 0", "queries": {}}
    for name, (df_api, df_sql, sql) in queries.items():
        res = _compare(df_api, df_sql)
        res["sql"] = " ".join(sql.split())
        out["queries"][name] = res
    out["pass"] = all(q["pass"] for q in out["queries"].values())
    out["per_class_rows"] = [
        r.asDict() for r in queries["per_class"][1].orderBy("class").collect()
    ]
    return out


# --------------------------------------------------------------------------
# F-6 격자 집계
# --------------------------------------------------------------------------
GRID_SQL = f"""
SELECT frame_id,
       CAST(FLOOR((xd - {config.ROI_X_MIN!r}) / {config.GRID_CELL!r}) AS INT) AS grid_x,
       CAST(FLOOR((yd - ({config.ROI_Y_MIN!r})) / {config.GRID_CELL!r}) AS INT) AS grid_y,
       COUNT(*)  AS n_points,
       SUM(zd)   AS z_sum,
       MIN(zd)   AS z_min,
       MAX(zd)   AS z_max
FROM (
    SELECT frame_id, CAST(x AS DOUBLE) AS xd, CAST(y AS DOUBLE) AS yd, CAST(z AS DOUBLE) AS zd
    FROM points
) p
WHERE NOT isnan(xd) AND NOT isnan(yd)
  AND NOT isnan(zd) AND zd <> CAST('Infinity' AS DOUBLE) AND zd <> CAST('-Infinity' AS DOUBLE)
  AND xd >= {config.ROI_X_MIN!r} AND xd < {config.ROI_X_MAX!r}
  AND yd >= {config.ROI_Y_MIN!r} AND yd < {config.ROI_Y_MAX!r}
GROUP BY frame_id,
         CAST(FLOOR((xd - {config.ROI_X_MIN!r}) / {config.GRID_CELL!r}) AS INT),
         CAST(FLOOR((yd - ({config.ROI_Y_MIN!r})) / {config.GRID_CELL!r}) AS INT)
"""


def compute_grid_stats(
    spark: "SparkSession",
    work: Path | str,
    frame_ids: Sequence[int] | None = None,
    out_path: Path | str | None = None,
) -> "DataFrame":
    """points Parquet에서 장면별 격자 통계를 Spark SQL로 집계해 grid_stats Parquet에 덮어쓴다.

    frame_ids가 있으면 ingest.points_files_for로 해당 파일만 읽은 뒤 frame_id로 거른다.
    Returns:
        저장한 grid_stats를 다시 읽은 DataFrame (kitti.schema.GRID_STATS).
    """
    from pyspark.sql import functions as F

    from . import schema

    read_points(spark, work, frame_ids).createOrReplaceTempView("points")
    agg = spark.sql(GRID_SQL)
    agg = agg.select(
        [F.col(f.name).cast(f.dataType).alias(f.name, metadata=f.metadata) for f in schema.GRID_STATS]
    )
    out = Path(out_path) if out_path is not None else _parquet_dir(work) / "grid_stats"
    agg.orderBy("frame_id", "grid_x", "grid_y").write.mode("overwrite").parquet(str(out))
    grid = spark.read.schema(schema.GRID_STATS).parquet(str(out))
    grid.createOrReplaceTempView("grid_stats")
    return grid


def processed_frame_ids(frames: "DataFrame") -> list[int]:
    """정상 처리된 프레임(frames.n_points IS NOT NULL)의 frame_id 오름차순 목록."""
    rows = frames.where("n_points IS NOT NULL").select("frame_id").orderBy("frame_id").collect()
    return [int(r.frame_id) for r in rows]


def cumulative_grid(spark: "SparkSession", grid: "DataFrame", n_frames: int) -> dict[str, Any]:
    """모든 프레임의 같은 격자를 합산한 80×80 밀집 배열(빈 격자 0)과 프레임 평균.

    Returns:
        {"sum": int64[nx, ny], "mean": float64[nx, ny], "n_frames": n_frames}
        배열 인덱스 [grid_x, grid_y].
    """
    import numpy as np

    grid.createOrReplaceTempView("grid_stats_cum")
    rows = spark.sql(
        "SELECT grid_x, grid_y, SUM(n_points) AS n FROM grid_stats_cum GROUP BY grid_x, grid_y"
    ).collect()
    total = np.zeros((config.GRID_NX, config.GRID_NY), dtype=np.int64)
    for r in rows:
        total[r.grid_x, r.grid_y] = r.n
    mean = total / n_frames if n_frames > 0 else np.zeros_like(total, dtype=np.float64)
    return {"sum": total, "mean": mean.astype(np.float64), "n_frames": n_frames}


def scene_grid(grid: "DataFrame", frame_id: int):
    """장면 하나의 n_points 80×80 밀집 배열(빈 격자 0)."""
    import numpy as np

    arr = np.zeros((config.GRID_NX, config.GRID_NY), dtype=np.int64)
    for r in grid.where(grid.frame_id == int(frame_id)).select("grid_x", "grid_y", "n_points").collect():
        arr[r.grid_x, r.grid_y] = r.n_points
    return arr


def pick_sample_frames(processed: Sequence[int], wanted: Sequence[int] = SAMPLE_FRAMES) -> list[int]:
    """wanted 중 처리된 장면을 쓰고, 모자라면 처리된 장면 앞에서부터 채운다(최대 len(wanted))."""
    have = set(processed)
    picked = [f for f in wanted if f in have]
    for f in processed:
        if len(picked) >= len(wanted):
            break
        if f not in picked:
            picked.append(f)
    return sorted(picked)


def grid_params(
    n_frames: int, sample: Sequence[int], frame_ids: Iterable[int] | None = None
) -> dict[str, Any]:
    """grid_params.json 내용. frame_ids를 주면 장면 집합(n, sha256)을 frame_set에 기록한다(verify가 비교)."""
    out = {
        "coordinate_system": "Velodyne LiDAR frame: x forward, y left, z up; origin = sensor; unit m",
        "roi": {"x_min": config.ROI_X_MIN, "x_max_exclusive": config.ROI_X_MAX,
                "y_min": config.ROI_Y_MIN, "y_max_exclusive": config.ROI_Y_MAX},
        "cell_size_m": config.GRID_CELL,
        "grid_shape": [config.GRID_NX, config.GRID_NY],
        "grid_x": f"FLOOR((CAST(x AS DOUBLE) - {config.ROI_X_MIN}) / {config.GRID_CELL})",
        "grid_y": f"FLOOR((CAST(y AS DOUBLE) - ({config.ROI_Y_MIN})) / {config.GRID_CELL})",
        "filter": "NaN/Inf (x, y, z) and ROI filter applied before FLOOR",
        "mean_definition": "per-cell sum of n_points over frames / n_frames_processed; empty cell = 0",
        "n_frames_processed": n_frames,
        "processed_frame_rule": "frames.n_points IS NOT NULL",
        "sample_frames": list(sample),
        "note": "Cumulative heatmap is a distribution relative to the sensor, not a geographic map.",
    }
    if frame_ids is not None:
        out["frame_set"] = config.frame_set_info(frame_ids)
    return out


# --------------------------------------------------------------------------
# F-7 조건별 분포 (DontCare 제외)
# --------------------------------------------------------------------------
F7_BASE = "SELECT * FROM object_features WHERE is_dontcare = false"

F7_SQL: dict[str, str] = {
    "class_dist": f"""
        SELECT class, dist_bin, COUNT(*) AS n_objects,
               COUNT(CASE WHEN valid_3d THEN 1 END) AS n_valid_3d,
               SUM(CAST(points_in_box AS BIGINT)) AS sum_points_in_box
        FROM ({F7_BASE}) GROUP BY class, dist_bin""",
    "class_occluded": f"""
        SELECT class, occluded, COUNT(*) AS n_objects,
               COUNT(CASE WHEN valid_3d THEN 1 END) AS n_valid_3d,
               SUM(CAST(points_in_box AS BIGINT)) AS sum_points_in_box
        FROM ({F7_BASE}) GROUP BY class, occluded""",
    "class_truncated": f"""
        SELECT class, trunc_bucket, COUNT(*) AS n_objects,
               COUNT(CASE WHEN valid_3d THEN 1 END) AS n_valid_3d,
               SUM(CAST(points_in_box AS BIGINT)) AS sum_points_in_box
        FROM (SELECT *, CASE WHEN truncated < 0 OR isnan(truncated) THEN 'invalid'
                             WHEN truncated = 0 THEN '0'
                             WHEN truncated <= 0.15 THEN '(0,0.15]'
                             WHEN truncated <= 0.30 THEN '(0.15,0.30]'
                             WHEN truncated <= 0.50 THEN '(0.30,0.50]'
                             ELSE '>0.50' END AS trunc_bucket
              FROM ({F7_BASE}))
        GROUP BY class, trunc_bucket""",
    "class_difficulty": f"""
        SELECT class, COUNT(*) AS n_objects,
               COUNT(CASE WHEN eligible_easy THEN 1 END) AS n_eligible_easy,
               COUNT(CASE WHEN eligible_moderate THEN 1 END) AS n_eligible_moderate,
               COUNT(CASE WHEN eligible_hard THEN 1 END) AS n_eligible_hard,
               COUNT(CASE WHEN diff_level = 4 THEN 1 END) AS n_not_eligible
        FROM ({F7_BASE}) GROUP BY class""",
}


def _class_rank(cls: str) -> int:
    return CLASS_ORDER.index(cls) if cls in CLASS_ORDER else len(CLASS_ORDER)


def compute_distributions(spark: "SparkSession") -> dict[str, Any]:
    """F-7 분포 표 4개를 pandas DataFrame으로 돌려준다(object_features 뷰, DontCare 제외)."""
    out: dict[str, Any] = {}
    for name, sql in F7_SQL.items():
        pdf = spark.sql(sql).toPandas()
        pdf["_rank"] = pdf["class"].map(_class_rank)
        second = [c for c in ("dist_bin", "occluded", "trunc_bucket") if c in pdf.columns]
        pdf = pdf.sort_values(["_rank", "class", *second]).drop(columns="_rank").reset_index(drop=True)
        out[name] = pdf
    return out


# --------------------------------------------------------------------------
# F-9 관측 부족
# --------------------------------------------------------------------------
THRESHOLD_SQL = """
SELECT class, n, k, points_in_box AS threshold
FROM (
    SELECT class, points_in_box,
           ROW_NUMBER() OVER (PARTITION BY class ORDER BY points_in_box ASC) AS rn,
           COUNT(*) OVER (PARTITION BY class) AS n,
           CAST((COUNT(*) OVER (PARTITION BY class) + 9) DIV 10 AS INT) AS k
    FROM {view}
    WHERE valid_3d AND points_in_box IS NOT NULL
) r
WHERE rn = k
"""


def compute_thresholds(df: "DataFrame") -> "DataFrame":
    """클래스별 기준값(class, n, k = ceil(n/10), threshold)을 정확히 계산한다.

    유효 3D 객체(valid_3d AND points_in_box IS NOT NULL)의 points_in_box를 오름차순 정렬해
    ROW_NUMBER = ceil(n/10)인 값을 고른다. 같은 값이 여러 개여도 정렬 위치의 값이므로 결과는 하나다.
    ceil(n/10)은 정수 연산 (n + 9) DIV 10으로 계산한다.
    """
    view = "f9_input"
    df.createOrReplaceTempView(view)
    return df.sparkSession.sql(THRESHOLD_SQL.format(view=view))


LOW_COLUMNS: tuple[str, ...] = (
    "class", "points_in_box", "frame_id", "object_id", "threshold", "dist_m", "dist_bin",
    "occluded", "truncated", "diff_level", "h", "w", "l",
)


def low_observation(df: "DataFrame", thresholds: "DataFrame") -> "DataFrame":
    """points_in_box <= 클래스 기준값인 유효 3D 객체 목록(class, points, frame_id, object_id 순 정렬)."""
    df.createOrReplaceTempView("f9_input")
    thresholds.createOrReplaceTempView("f9_thresholds")
    cols = ", ".join(f"o.`{c}`" if c != "threshold" else "t.threshold" for c in LOW_COLUMNS)
    return df.sparkSession.sql(
        f"""
        SELECT {cols}
        FROM f9_input o JOIN f9_thresholds t ON o.class = t.class
        WHERE o.valid_3d AND o.points_in_box IS NOT NULL AND o.points_in_box <= t.threshold
        ORDER BY o.class, o.points_in_box, o.frame_id, o.object_id
        """
    )


def low_scenes(low: "DataFrame") -> "DataFrame":
    """관측 부족 객체가 있는 장면(중복 없는 frame_id)과 건수."""
    low.createOrReplaceTempView("f9_low")
    return low.sparkSession.sql(
        """
        SELECT frame_id, COUNT(*) AS n_low_objects,
               CONCAT_WS(';', ARRAY_SORT(COLLECT_SET(class))) AS classes
        FROM f9_low GROUP BY frame_id ORDER BY frame_id
        """
    )


# --------------------------------------------------------------------------
# 그림 (matplotlib Agg)
# --------------------------------------------------------------------------
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.dpi": 120, "savefig.dpi": 150, "font.size": 10})
    return plt


def _caption(fig, text: str) -> None:
    import textwrap

    width = int(fig.get_figwidth() * 17)  # 7 pt 글자 기준 대략적인 한 줄 글자 수
    body = "\n".join(textwrap.wrap(text, width))
    fig.text(0.01, 0.005, f"{body}\n{SOURCE_CAPTION}", fontsize=7, color="#444444", ha="left", va="bottom")


def _bev_axes(ax) -> None:
    ax.set_xlim(config.ROI_Y_MAX, config.ROI_Y_MIN)  # +y(좌측)를 왼쪽에 둔다
    ax.set_ylim(config.ROI_X_MIN, config.ROI_X_MAX)
    ax.set_xlabel("y (m, left = +)")
    ax.set_ylabel("x (m, forward)")
    ax.plot([0], [0], marker="^", color="red", markersize=8, markeredgecolor="white", zorder=3)
    ax.annotate("sensor (0, 0)", (0, 0), xytext=(3, 3), color="red", fontsize=8)
    ax.set_aspect("equal")


def _bev_image(ax, arr, norm, cmap):
    import numpy as np

    data = np.ma.masked_less_equal(arr.astype(np.float64), 0.0)
    extent = (config.ROI_Y_MIN, config.ROI_Y_MAX, config.ROI_X_MIN, config.ROI_X_MAX)
    return ax.imshow(data, origin="lower", extent=extent, norm=norm, cmap=cmap, interpolation="nearest")


def plot_cumulative(cum: dict[str, Any], path: Path) -> None:
    import numpy as np
    from matplotlib.colors import LogNorm

    plt = _plt()
    mean = cum["mean"]
    fig, ax = plt.subplots(figsize=(7, 7.6))
    cmap = plt.get_cmap("viridis").with_extremes(bad="#f2f2f2")
    pos = mean[mean > 0]
    norm = LogNorm(vmin=float(pos.min()), vmax=float(pos.max())) if pos.size else None
    im = _bev_image(ax, mean, norm, cmap)
    _bev_axes(ax)
    ax.set_title(f"Mean LiDAR points per 1 m cell over {cum['n_frames']:,} frames (BEV)")
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("points per cell per frame (log scale)")
    _caption(
        fig,
        f"Mean = cell sum / {cum['n_frames']:,} processed frames; empty cells = 0 (light grey). "
        f"ROI x∈[0,80), y∈[-40,40) m. Total points in ROI: {int(np.sum(cum['sum'])):,}. "
        "Sensor-relative distribution, not a geographic map.",
    )
    fig.subplots_adjust(bottom=0.13)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_samples(arrays: dict[int, Any], path: Path) -> None:
    from matplotlib.colors import LogNorm

    plt = _plt()
    n = max(len(arrays), 1)
    fig, axes = plt.subplots(1, n, figsize=(5.2 * n, 6.2), squeeze=False)
    cmap = plt.get_cmap("viridis").with_extremes(bad="#f2f2f2")
    vmax = max((int(a.max()) for a in arrays.values()), default=1)
    norm = LogNorm(vmin=1, vmax=max(vmax, 2))
    im = None
    for ax, (fid, arr) in zip(axes[0], sorted(arrays.items())):
        im = _bev_image(ax, arr, norm, cmap)
        _bev_axes(ax)
        ax.set_title(f"frame_id {fid:06d}: {int(arr.sum()):,} points in ROI")
    fig.suptitle("Sample scenes: LiDAR points per 1 m BEV cell")
    _caption(fig, "Per-scene grid_stats.n_points; empty cells light grey. Velodyne frame, sensor at origin.")
    fig.subplots_adjust(bottom=0.15, top=0.88, right=0.9, wspace=0.3)
    if im is not None:
        cax = fig.add_axes((0.92, 0.2, 0.012, 0.62))
        cb = fig.colorbar(im, cax=cax)
        cb.set_label("points per 1 m cell (log scale)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def _share_table(pdf, key: str, levels: Sequence[Any]):
    """class × key 건수 표를 클래스 내 비율(%)로 바꾼 피벗(행 class, 열 levels)."""
    pv = pdf.pivot_table(index="class", columns=key, values="n_objects", aggfunc="sum", fill_value=0)
    pv = pv.reindex(columns=[lv for lv in levels if lv in pv.columns], fill_value=0)
    pv = pv.loc[sorted(pv.index, key=_class_rank)]
    totals = pv.sum(axis=1)
    return pv.div(totals.where(totals > 0, 1), axis=0) * 100.0, totals


def plot_class_dist(pdf, path: Path) -> None:
    import numpy as np

    plt = _plt()
    share, totals = _share_table(pdf, "dist_bin", config.DIST_BIN_LABELS)
    fig, ax = plt.subplots(figsize=(9, 5))
    n_cls = len(share.index)
    width = 0.8 / max(n_cls, 1)
    xs = np.arange(len(share.columns))
    for i, cls in enumerate(share.index):
        ax.bar(xs + (i - (n_cls - 1) / 2) * width, share.loc[cls].values, width,
               color=CLASS_COLORS.get(cls, "#333333"), edgecolor="#333333", linewidth=0.4,
               label=f"{cls} (n={int(totals[cls]):,})")
    ax.set_xticks(xs, [f"{c} m" for c in share.columns])
    ax.set_xlabel("Camera-frame horizontal distance √(x²+z²) bin")
    ax.set_ylabel("Share of objects within class (%)")
    ax.set_title("Objects by class and distance (DontCare excluded)")
    ax.legend(fontsize=8, ncol=2)
    _caption(fig, "Bins: <10, 10–20, 20–40, ≥40 m (same as DB fn_dist_bin).")
    fig.subplots_adjust(bottom=0.2)
    fig.savefig(path)
    plt.close(fig)


def plot_stacked(pdf, key: str, levels: Sequence[Any], labels: Sequence[str], title: str,
                 legend_title: str, caption: str, path: Path) -> None:
    import numpy as np

    plt = _plt()
    share, totals = _share_table(pdf, key, levels)
    cmap = plt.get_cmap("viridis")
    cols = list(share.columns)
    fig, ax = plt.subplots(figsize=(9, 5))
    bottom = np.zeros(len(share.index))
    for j, lv in enumerate(cols):
        color = cmap(j / max(len(cols) - 1, 1))
        vals = share[lv].values
        ax.bar(range(len(share.index)), vals, bottom=bottom, color=color, edgecolor="white",
               linewidth=0.5, label=labels[list(levels).index(lv)])
        bottom += vals
    ax.set_xticks(range(len(share.index)),
                  [f"{c}\n(n={int(totals[c]):,})" for c in share.index], fontsize=8)
    ax.set_xlabel("Object class")
    ax.set_ylabel("Share of objects within class (%)")
    ax.set_ylim(0, 100)
    ax.set_title(title)
    ax.legend(title=legend_title, fontsize=8, bbox_to_anchor=(1.01, 1), loc="upper left")
    _caption(fig, caption)
    fig.subplots_adjust(bottom=0.22, right=0.8)
    fig.savefig(path)
    plt.close(fig)


def plot_difficulty(pdf, path: Path) -> None:
    import numpy as np

    plt = _plt()
    pdf = pdf.copy()
    pdf["_rank"] = pdf["class"].map(_class_rank)
    pdf = pdf.sort_values("_rank")
    cmap = plt.get_cmap("viridis")
    levels = [("n_eligible_easy", "Easy"), ("n_eligible_moderate", "Moderate"), ("n_eligible_hard", "Hard")]
    xs = np.arange(len(pdf))
    width = 0.27
    fig, ax = plt.subplots(figsize=(9, 5))
    denom = pdf["n_objects"].where(pdf["n_objects"] > 0, 1).values
    for j, (col, name) in enumerate(levels):
        ax.bar(xs + (j - 1) * width, pdf[col].values / denom * 100.0, width,
               color=cmap(j / 2), edgecolor="#333333", linewidth=0.4, label=name)
    ax.set_xticks(xs, [f"{c}\n(n={n:,})" for c, n in zip(pdf["class"], pdf["n_objects"])], fontsize=8)
    ax.set_xlabel("Object class")
    ax.set_ylabel("Objects eligible for difficulty (%)")
    ax.set_ylim(0, 100)
    ax.set_title("KITTI difficulty eligibility by class (cumulative levels)")
    ax.legend(title="Eligible for", fontsize=8)
    _caption(fig, "Easy: bbox h≥40 px, occ≤0, trunc≤0.15; Moderate: ≥25 px, occ≤1, trunc≤0.30; "
                  "Hard: ≥25 px, occ≤2, trunc≤0.50.")
    fig.subplots_adjust(bottom=0.22)
    fig.savefig(path)
    plt.close(fig)


def plot_points_distribution(values: dict[str, list[int]], thr: dict[str, dict[str, int]], path: Path) -> None:
    import numpy as np

    plt = _plt()
    classes = sorted(values, key=_class_rank)
    n = max(len(classes), 1)
    ncols = min(4, n)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3.2 * nrows), squeeze=False)
    for ax in axes.ravel()[n:]:
        ax.set_visible(False)
    for ax, cls in zip(axes.ravel(), classes):
        v = np.asarray(values[cls], dtype=np.float64) + 1.0
        bins = np.logspace(0, math.log10(max(v.max(), 10.0)) + 0.05, 30)
        ax.hist(v, bins=bins, color=CLASS_COLORS.get(cls, "#333333"), edgecolor="#333333", linewidth=0.3)
        ax.set_xscale("log")
        t = thr[cls]
        ax.axvline(t["threshold"] + 1.0, color="black", linestyle="--", linewidth=1.2)
        ax.set_title(f"{cls}: n={t['n']:,}, threshold ≤ {t['threshold']} pts", fontsize=9)
        ax.set_xlabel("points in 3D box + 1 (log scale)")
        ax.set_ylabel("objects")
    fig.suptitle("Points inside 3D box by class, with low-observation threshold (dashed)")
    _caption(fig, "Valid 3D objects only. Threshold = ceil(n/10)-th smallest points_in_box per class "
                  "(= MySQL NTILE(10) bucket 1 max). Low observation: points ≤ threshold.")
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------
# 전체 실행
# --------------------------------------------------------------------------
def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


class AnalysisRefused(ValueError):
    """부분집합 실행이 정본 결과(저장소 results/, <work>/parquet)를 덮어쓰려 할 때."""


def _same_path(a: Path, b: Path) -> bool:
    return a.expanduser().resolve() == b.expanduser().resolve()


def resolve_outputs(
    work: Path | str,
    results_dir: Path | str,
    frame_ids: Sequence[int] | None,
    parquet_out: Path | str | None = None,
    subset_name: str | None = None,
) -> Path:
    """파생 Parquet(grid_stats·low_observation) 출력 폴더를 정하고 부분집합 보호 규칙을 검사한다.

    Raises:
        AnalysisRefused: 부분집합인데 results_dir가 기본 RESULTS_DIR이거나 출력이 정본 <work>/parquet일 때.
    """
    canonical = _parquet_dir(work)
    if frame_ids is None:
        return Path(parquet_out).expanduser() if parquet_out is not None else canonical
    if _same_path(Path(results_dir), RESULTS_DIR):
        raise AnalysisRefused(
            f"frame subset given but --results is the canonical {RESULTS_DIR}; use a separate results dir"
        )
    if parquet_out is None:
        fs = config.frame_set_info(frame_ids)
        name = subset_name or f"frames-{fs['n']}-{str(fs['sha256'])[:12]}"
        return Path(work).expanduser() / "subsets" / name
    out = Path(parquet_out).expanduser()
    if _same_path(out, canonical):
        raise AnalysisRefused(
            f"frame subset given but --parquet-out is the canonical {canonical}; "
            "use <work>/subsets/<name> (default) or another dir"
        )
    return out


def run(
    spark: "SparkSession",
    work: Path | str,
    results_dir: Path | str = RESULTS_DIR,
    figures_dir: Path | str | None = None,
    frame_ids: Sequence[int] | None = None,
    *,
    parquet_out: Path | str | None = None,
    subset_name: str | None = None,
) -> dict[str, Any]:
    """F-5·F-6·F-7·F-9를 실행해 결과 파일을 쓰고 analysis_summary dict를 돌려준다.

    입력은 항상 <work>/parquet(정본)에서 읽는다. 파생 Parquet(grid_stats, low_observation)은
    resolve_outputs가 정한 폴더에 쓰며, 그 폴더가 정본이 아니면 대상 장면의 object_features 사본도
    함께 써서 verify --spark-work로 그 폴더 하나만 가리키면 되게 한다.

    Raises:
        AnalysisRefused: 부분집합 실행이 정본 결과를 덮어쓰려 할 때(아무것도 쓰기 전에 검사).
    """
    import numpy as np
    import pandas as pd

    work = Path(work).expanduser()
    results = Path(results_dir).expanduser()
    ids = sorted({int(f) for f in frame_ids}) if frame_ids is not None else None
    out_root = resolve_outputs(work, results, ids, parquet_out, subset_name)
    figures = Path(figures_dir).expanduser() if figures_dir is not None else results / "figures"
    results.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    t = time.perf_counter()
    dfs = register_views(spark, work, ids)
    if ids is not None:
        frame_set_ids: list[int] = ids
    else:
        frame_set_ids = [int(r.frame_id) for r in dfs["frames"].select("frame_id").collect()]
    frame_set = config.frame_set_info(frame_set_ids)
    if not _same_path(out_root, _parquet_dir(work)):
        out_root.mkdir(parents=True, exist_ok=True)
        dfs["object_features"].write.mode("overwrite").parquet(str(out_root / "object_features"))

    # F-5
    f5 = f5_compare(spark)
    _write_json(results / "f5_dataframe_vs_sql.json", f5)
    timings["f5"] = round(time.perf_counter() - t, 3)
    log.info("F-5 pass=%s", f5["pass"])

    # F-6
    t = time.perf_counter()
    grid = compute_grid_stats(spark, work, ids, out_path=out_root / "grid_stats")
    processed = processed_frame_ids(dfs["frames"])
    cum = cumulative_grid(spark, grid, len(processed))
    gx, gy = np.meshgrid(np.arange(config.GRID_NX), np.arange(config.GRID_NY), indexing="ij")
    pd.DataFrame(
        {
            "grid_x": gx.ravel(),
            "grid_y": gy.ravel(),
            "x_min_m": config.ROI_X_MIN + gx.ravel() * config.GRID_CELL,
            "y_min_m": config.ROI_Y_MIN + gy.ravel() * config.GRID_CELL,
            "n_points_sum": cum["sum"].ravel(),
            "n_points_mean": cum["mean"].ravel(),
        }
    ).to_csv(results / "grid_cumulative.csv", index=False)
    sample = pick_sample_frames(processed)
    _write_json(results / "grid_params.json", grid_params(len(processed), sample, frame_ids=frame_set_ids))
    plot_cumulative(cum, figures / "f6_grid_cumulative.png")
    plot_samples({f: scene_grid(grid, f) for f in sample}, figures / "f6_sample_scenes.png")
    timings["f6"] = round(time.perf_counter() - t, 3)

    # F-7
    t = time.perf_counter()
    dist = compute_distributions(spark)
    for name, pdf in dist.items():
        pdf.to_csv(results / f"f7_{name}.csv", index=False)
    plot_class_dist(dist["class_dist"], figures / "f7_class_distance.png")
    plot_stacked(dist["class_occluded"], "occluded", [0, 1, 2, 3],
                 ["0 fully visible", "1 partly occluded", "2 largely occluded", "3 unknown"],
                 "Occlusion level by class (DontCare excluded)", "occluded",
                 "KITTI label 'occluded' field (0–3).", figures / "f7_class_occluded.png")
    plot_stacked(dist["class_truncated"], "trunc_bucket", [*TRUNC_BUCKETS, "invalid"],
                 [*TRUNC_BUCKETS, "invalid (<0)"],
                 "Truncation ratio by class (DontCare excluded)", "truncated (ratio)",
                 "KITTI label 'truncated' field (0–1), bucketed.", figures / "f7_class_truncated.png")
    plot_difficulty(dist["class_difficulty"], figures / "f7_class_difficulty.png")
    timings["f7"] = round(time.perf_counter() - t, 3)

    # F-9
    t = time.perf_counter()
    feats = dfs["object_features"]
    thr_df = compute_thresholds(feats)
    thr_pdf = thr_df.toPandas()
    thr_pdf["_rank"] = thr_pdf["class"].map(_class_rank)
    thr_pdf = thr_pdf.sort_values("_rank").drop(columns="_rank").reset_index(drop=True)
    thr_pdf.to_csv(results / "f9_thresholds.csv", index=False)
    low = low_observation(feats, thr_df)
    low_path = out_root / "low_observation"
    low.coalesce(1).sortWithinPartitions("class", "points_in_box", "frame_id", "object_id") \
        .write.mode("overwrite").parquet(str(low_path))
    low_saved = spark.read.parquet(str(low_path)).orderBy("class", "points_in_box", "frame_id", "object_id")
    low_pdf = low_saved.toPandas()
    low_pdf.to_csv(results / "f9_low_objects.csv", index=False)
    scenes_pdf = low_scenes(low_saved).toPandas()
    scenes_pdf.to_csv(results / "f9_low_scenes.csv", index=False)
    vals_rows = feats.where("valid_3d AND points_in_box IS NOT NULL").select("class", "points_in_box").collect()
    values: dict[str, list[int]] = {}
    for r in vals_rows:
        values.setdefault(r["class"], []).append(int(r["points_in_box"]))
    thr_map = {
        r["class"]: {"n": int(r["n"]), "k": int(r["k"]), "threshold": int(r["threshold"])}
        for r in thr_pdf.to_dict("records")
    }
    plot_points_distribution(values, thr_map, figures / "f9_points_distribution.png")
    timings["f9"] = round(time.perf_counter() - t, 3)

    # 요약
    cls_rows = {r["class"]: r for r in f5["per_class_rows"]}
    cd = dist["class_dist"]
    within20 = {}
    for cls, g in cd.groupby("class"):
        tot = int(g["n_objects"].sum())
        near = int(g.loc[g["dist_bin"].isin(["0-10", "10-20"]), "n_objects"].sum())
        within20[cls] = round(100.0 * near / tot, 2) if tot else None
    total_roi = int(np.sum(cum["sum"]))
    near_rows = int(np.sum(cum["sum"][: int(20 / config.GRID_CELL), :]))
    peak = np.unravel_index(int(np.argmax(cum["mean"])), cum["mean"].shape)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "work": config.display_path(work),
        "frame_subset": len(ids) if ids is not None else "all",
        "frame_set": frame_set,
        "parquet_out": config.display_path(out_root),
        "n_frames": dfs["frames"].count(),
        "n_frames_processed": len(processed),
        "n_objects_non_dontcare": int(sum(r["n_objects"] for c, r in cls_rows.items() if c != config.DONTCARE)),
        "n_dontcare": int(cls_rows.get(config.DONTCARE, {}).get("n_objects", 0)),
        "objects_per_class": {c: int(r["n_objects"]) for c, r in cls_rows.items() if c != config.DONTCARE},
        "f5_pass": f5["pass"],
        "f6": {
            "grid_rows": grid.count(),
            "total_points_in_roi": total_roi,
            "share_points_x_lt_20m_pct": round(100.0 * near_rows / total_roi, 2) if total_roi else None,
            "peak_cell": {"grid_x": int(peak[0]), "grid_y": int(peak[1]),
                          "mean_points": float(cum["mean"][peak])},
            "sample_frames": sample,
        },
        "f7": {"share_within_20m_pct": within20},
        "f9": {
            "thresholds": thr_map,
            "n_low_objects": int(len(low_pdf)),
            "n_low_scenes": int(len(scenes_pdf)),
            "n_low_by_class": {str(k): int(v) for k, v in low_pdf.groupby("class").size().items()},
        },
        "timings_s": timings,
        "source": SOURCE_CAPTION,
    }
    _write_json(results / "analysis_summary.json", summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KITTI Spark 분석 (F-5, F-6, F-7, F-9)")
    parser.add_argument("--work", default=str(config.KITTI_WORK), help="Parquet 산출물 폴더")
    parser.add_argument("--results", default=str(RESULTS_DIR))
    parser.add_argument("--figures", default=None, help="그림 폴더 (기본 <results>/figures)")
    parser.add_argument("--master", default="local[8]")
    parser.add_argument("--frames-file", help="분석할 frame_id 목록 파일(한 줄에 하나). --results 별도 지정 필수")
    parser.add_argument("--parquet-out", default=None,
                        help="파생 Parquet 폴더 (기본: 전체 <work>/parquet, 부분집합 <work>/subsets/<name>)")
    parser.add_argument("--subset-name", default=None, help="부분집합 이름 (기본 목록 파일 이름)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    ids: list[int] | None = ingest.read_frames_file(args.frames_file) if args.frames_file else None
    subset_name = args.subset_name or (Path(args.frames_file).stem if args.frames_file else None)
    try:  # 거부 조건은 Spark를 띄우기 전에 검사한다
        resolve_outputs(args.work, args.results, ids, args.parquet_out, subset_name)
    except AnalysisRefused as exc:
        log.error("refused: %s", exc)
        return 2
    config.KITTI_WORK = Path(args.work).expanduser()  # spark.local.dir = <work>/spark-tmp
    spark = config.get_spark(args.master, app="kitti-analysis")
    try:
        summary = run(spark, args.work, args.results, args.figures, ids,
                      parquet_out=args.parquet_out, subset_name=subset_name)
    finally:
        spark.stop()
    log.info("done: f5_pass=%s low_objects=%d low_scenes=%d", summary["f5_pass"],
             summary["f9"]["n_low_objects"], summary["f9"]["n_low_scenes"])
    return 0 if summary["f5_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
