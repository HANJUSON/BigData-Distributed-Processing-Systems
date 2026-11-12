"""kitti.analysis 시험: 합성 데이터로 F-5, F-6, F-7, F-9 확인 (Spark local[2])."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from conftest import CALIB_000000
from kitti import analysis, config, ingest
from kitti import geometry as geo

CAR = "Car 0.00 0 -1.50 10.00 5.00 40.00 30.00 1.50 1.60 3.90 1.00 1.70 20.00 -1.50"
NAN = np.float32(np.nan)

# 경계 사례 (x, y, z): 기대 격자 (None = ROI 밖)
EDGE_POINTS: list[tuple[tuple[float, float, float], tuple[int, int] | None]] = [
    ((0.0, 0.0, 0.5), (0, 40)),  # x=0 포함
    ((80.0, 0.0, 0.5), None),  # x=80 제외
    ((10.0, -40.0, -1.0), (10, 0)),  # y=-40 포함
    ((10.0, 40.0, -1.0), None),  # y=40 제외
    ((-0.001, 5.0, 0.0), None),  # x<0 제외
    ((float("nan"), 5.0, 0.0), None),  # NaN 제외
    ((5.0, float("nan"), 0.0), None),
    ((float("inf"), 5.0, 0.0), None),
    ((5.5, -0.5, 1.25), (5, 39)),  # 음수 y → floor(39.5) = 39
    ((5.5, -39.2, 0.75), (5, 0)),
    ((3.0, -12.7, -0.25), (3, 27)),
    ((79.999, 39.999, 2.0), (79, 79)),
    ((7.2, -1e-7, 0.1), (7, 39)),  # -0 근처 음수 → 39
    ((7.2, 0.0, 0.3), (7, 40)),
    ((6.5, 2.5, float("nan")), None),  # z NaN 제외 (L1)
    ((6.5, 2.5, float("inf")), None),  # z Inf 제외
    ((6.5, 2.5, float("-inf")), None),
    ((6.5, 2.5, 0.125), (6, 42)),  # 같은 격자의 정상 점: z 통계가 오염되지 않아야 함
]


def _frame0_points() -> np.ndarray:
    pts = np.array([[x, y, z, 0.5] for (x, y, z), _ in EDGE_POINTS], dtype=np.float32)
    return pts


def _frame1_points() -> np.ndarray:
    rng = np.random.default_rng(1)
    n = 600
    return np.column_stack(
        [rng.uniform(-5, 85, n), rng.uniform(-45, 45, n), rng.uniform(-2, 2, n), rng.uniform(0, 1, n)]
    ).astype(np.float32)


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    config.KITTI_WORK = tmp_path_factory.mktemp("sparkwork")
    session = config.get_spark("local[2]", app="kitti-analysis-test")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """프레임 0(경계 점), 1(무작위 점), 2(빈 .bin, 정상 처리), 3(velodyne 없음, 미처리)."""
    root = tmp_path_factory.mktemp("kitti")
    base = root / "training"
    for sub in config.SUBDIRS:
        (base / sub).mkdir(parents=True)
    points = {0: _frame0_points(), 1: _frame1_points(), 2: np.zeros((0, 4), dtype=np.float32)}
    for fid in range(4):
        name = f"{fid:06d}"
        Image.new("RGB", (100, 50)).save(base / "image_2" / f"{name}.png")
        (base / "calib" / f"{name}.txt").write_text(CALIB_000000, encoding="utf-8")
        (base / "label_2" / f"{name}.txt").write_text(CAR + "\n", encoding="utf-8")
        if fid in points:
            points[fid].tofile(base / "velodyne" / f"{name}.bin")
    return {"root": root, "points": points}


def _feature_row(fid: int, oid: int, cls: str, pts: int | None, *, occluded: int = 0,
                 truncated: float = 0.0, dist: float | None = 5.0, valid: bool | None = None) -> tuple:
    dontcare = cls == config.DONTCARE
    valid = (not dontcare) if valid is None else valid
    dist = None if dontcare else dist
    bbox_h = 50.0
    d = geo.difficulty(bbox_h, occluded, truncated, dontcare)
    e, m, h, lvl = d["eligible_easy"], d["eligible_moderate"], d["eligible_hard"], d["diff_level"]
    return (
        fid, oid, cls, truncated, occluded, 0.0, 0.0, 0.0, 10.0, bbox_h,
        1.5, 1.6, 3.9, 0.0, 1.0, 5.0 if valid else -1.0, 0.0,
        dontcare, valid, dist, bbox_h, e, m, h, lvl,
        pts if valid else None, None if dist is None else geo.dist_bin(dist),
    )


# 클래스별 points_in_box: 동점·n<10·n=10·n=11 사례
F9_VALUES: dict[str, list[int]] = {
    "Car": [7, 3, 3, 0, 12, 3, 5, 5, 9, 1, 3, 20, 4, 4, 8, 2, 2, 6, 11, 3, 0, 15, 2],  # n=23, k=3
    "Pedestrian": [9, 4, 4, 30, 2],  # n=5, k=1
    "Cyclist": [5, 5, 5, 5, 5, 5, 5, 5, 5, 5],  # n=10, k=1, 전부 동점
    "Van": [10, 1, 2, 3, 4, 5, 6, 7, 8, 9, 1],  # n=11, k=2, 동점 1
}


def _feature_rows() -> list[tuple]:
    rows: list[tuple] = []
    oid = {f: 0 for f in range(4)}

    def nxt(fid: int) -> int:
        oid[fid] += 1
        return oid[fid]

    i = 0
    for cls, vals in F9_VALUES.items():
        for v in vals:
            fid = i % 3
            i += 1
            occ = i % 4
            trunc = (0.0, 0.15, 0.3, 0.5, 0.51, 0.07)[i % 6]
            dist = (3.0, 10.0, 19.99, 25.0, 40.0, 55.5)[i % 6]
            rows.append(_feature_row(fid, nxt(fid), cls, v, occluded=occ, truncated=trunc, dist=dist))
    # 무효 3D 일반 객체(NULL 포인트), DontCare — F-9 대상 아님
    rows.append(_feature_row(1, nxt(1), "Car", None, valid=False, dist=2.0))
    rows.append(_feature_row(0, nxt(0), config.DONTCARE, None))
    rows.append(_feature_row(3, nxt(3), config.DONTCARE, None))
    rows.append(_feature_row(3, nxt(3), "Truck", 50, occluded=3, truncated=0.9, dist=70.0))
    return rows


@pytest.fixture(scope="module")
def work(tree: dict, spark, tmp_path_factory: pytest.TempPathFactory) -> Path:
    from kitti import schema

    w = tmp_path_factory.mktemp("work")
    ingest.run(tree["root"], w, master="local[2]", n_partitions=2,
               results_dir=tmp_path_factory.mktemp("qres"), spark=spark, allow_incomplete=True)
    spark.createDataFrame(_feature_rows(), schema.OBJECT_FEATURES) \
        .write.mode("overwrite").parquet(str(w / "parquet" / "object_features"))
    return w


@pytest.fixture(scope="module")
def views(work: Path, spark) -> dict:
    return analysis.register_views(spark, work)


# --------------------------------------------------------------------------
# F-5
# --------------------------------------------------------------------------
def test_f5_dataframe_equals_sql(views: dict, spark) -> None:
    res = analysis.f5_compare(spark)
    assert res["pass"], res
    for name, q in res["queries"].items():
        assert q["rows_dataframe"] == q["rows_sql"] > 0, name
        assert q["except_dataframe_minus_sql"] == 0 and q["except_sql_minus_dataframe"] == 0
    per_class = {r["class"]: r for r in res["per_class_rows"]}
    assert per_class["Car"]["n_objects"] == len(F9_VALUES["Car"]) + 1
    assert per_class["Car"]["n_valid_3d"] == len(F9_VALUES["Car"])
    assert per_class[config.DONTCARE]["mean_dist_m"] is None
    assert res["queries"]["per_scene"]["rows_sql"] == 4


def test_f5_compare_detects_difference(views: dict, spark) -> None:
    a = spark.table("frames").select("frame_id")
    b = a.where("frame_id > 0")
    assert not analysis._compare(a, b)["pass"]


# --------------------------------------------------------------------------
# F-6
# --------------------------------------------------------------------------
def _grid_dict(grid_df, fid: int) -> dict:
    return {
        (r.grid_x, r.grid_y): (r.n_points, r.z_sum, r.z_min, r.z_max)
        for r in grid_df.where(grid_df.frame_id == fid).collect()
    }


def _numpy_dict(pts: np.ndarray) -> dict:
    ref = geo.grid_cell_counts(pts)
    return {
        (int(gx), int(gy)): (int(n), float(s), float(lo), float(hi))
        for gx, gy, n, s, lo, hi in zip(*(ref[k] for k in ("grid_x", "grid_y", "n_points", "z_sum", "z_min", "z_max")))
    }


def test_grid_edges_match_expected_and_numpy(work: Path, tree: dict, views: dict, spark) -> None:
    from kitti import schema

    grid = analysis.compute_grid_stats(spark, work)
    got = _grid_dict(grid, 0)
    expected_cells: dict = {}
    for (x, y, z), cell in EDGE_POINTS:
        if cell is not None:
            expected_cells.setdefault(cell, []).append(np.float64(np.float32(z)))
    assert set(got) == set(expected_cells)
    for cell, zs in expected_cells.items():
        n, s, lo, hi = got[cell]
        assert n == len(zs) and lo == min(zs) and hi == max(zs)
    # numpy 순수 함수와 정확히 같다(정수 완전 일치, z 통계 float 일치)
    for fid in (0, 1):
        assert got_eq(_grid_dict(grid, fid), _numpy_dict(tree["points"][fid]))
    assert grid.where("frame_id = 2").count() == 0
    assert grid.schema.simpleString() == schema.GRID_STATS.simpleString()
    assert (work / "parquet" / "grid_stats").is_dir()


def got_eq(a: dict, b: dict) -> bool:
    assert set(a) == set(b)
    for k in a:
        assert a[k][0] == b[k][0], k  # n_points 정수 완전 일치
        assert a[k][2] == b[k][2] and a[k][3] == b[k][3], k  # z_min/z_max 정확히 일치
        assert a[k][1] == b[k][1], (k, a[k][1], b[k][1])  # z_sum 같은 순서 합
    return True


def test_grid_subset_reads_only_requested_frames(work: Path, tree: dict, spark, tmp_path: Path) -> None:
    grid = analysis.compute_grid_stats(spark, work, frame_ids=[1], out_path=tmp_path / "gs")
    assert {r.frame_id for r in grid.select("frame_id").distinct().collect()} == {1}
    assert got_eq(_grid_dict(grid, 1), _numpy_dict(tree["points"][1]))
    empty = analysis.compute_grid_stats(spark, work, frame_ids=[3], out_path=tmp_path / "gs3")
    assert empty.count() == 0


def test_cumulative_mean_uses_processed_frames(work: Path, tree: dict, views: dict, spark) -> None:
    grid = analysis.compute_grid_stats(spark, work)
    processed = analysis.processed_frame_ids(views["frames"])
    assert processed == [0, 1, 2]  # 3은 velodyne 없음, 2는 빈 .bin이지만 정상 처리
    cum = analysis.cumulative_grid(spark, grid, len(processed))
    ref = np.zeros((config.GRID_NX, config.GRID_NY), dtype=np.int64)
    for fid in (0, 1):
        r = geo.grid_cell_counts(tree["points"][fid])
        np.add.at(ref, (r["grid_x"], r["grid_y"]), r["n_points"])
    assert cum["sum"].shape == (80, 80)
    np.testing.assert_array_equal(cum["sum"], ref)
    np.testing.assert_array_equal(cum["mean"], ref / 3.0)
    assert (ref == 0).any() and (cum["mean"][ref == 0] == 0.0).all()  # 빈 격자 = 0


def test_pick_sample_frames() -> None:
    assert analysis.pick_sample_frames([0, 5, 1000, 5000, 7000]) == [0, 1000, 5000]
    assert analysis.pick_sample_frames([0, 1, 2]) == [0, 1, 2]
    assert analysis.pick_sample_frames([3, 4, 1000, 9]) == [3, 4, 1000]


# --------------------------------------------------------------------------
# F-7
# --------------------------------------------------------------------------
def test_f7_excludes_dontcare_and_buckets(views: dict, spark) -> None:
    dist = analysis.compute_distributions(spark)
    n_non_dc = sum(1 for r in _feature_rows() if r[2] != config.DONTCARE)
    for name, pdf in dist.items():
        assert config.DONTCARE not in set(pdf["class"]), name
        assert int(pdf["n_objects"].sum()) == n_non_dc, name
    tb = dist["class_truncated"]
    rows = [r for r in _feature_rows() if r[2] != config.DONTCARE]

    def bucket(t: float) -> str:
        if t == 0:
            return "0"
        if t <= 0.15:
            return "(0,0.15]"
        if t <= 0.30:
            return "(0.15,0.30]"
        if t <= 0.50:
            return "(0.30,0.50]"
        return ">0.50"

    expected = pd.Series([bucket(r[3]) for r in rows]).value_counts().to_dict()
    assert tb.groupby("trunc_bucket")["n_objects"].sum().to_dict() == expected
    cd = dist["class_dist"]
    exp_bins = pd.Series([r[26] for r in rows]).value_counts().to_dict()
    assert cd.groupby("dist_bin")["n_objects"].sum().to_dict() == exp_bins
    diff = dist["class_difficulty"].set_index("class")
    car = [r for r in rows if r[2] == "Car"]
    assert diff.loc["Car", "n_eligible_easy"] == sum(r[21] for r in car)
    assert diff.loc["Car", "n_eligible_hard"] == sum(r[23] for r in car)


# --------------------------------------------------------------------------
# F-9
# --------------------------------------------------------------------------
def _reference_threshold(vals: list[int]) -> tuple[int, int, int]:
    s = sorted(vals)
    n = len(s)
    k = math.ceil(n / 10)
    return n, k, s[k - 1]


def test_f9_thresholds_exact(views: dict, spark) -> None:
    thr = {r["class"]: r for r in analysis.compute_thresholds(views["object_features"]).collect()}
    assert set(thr) == set(F9_VALUES) | {"Truck"}
    for cls, vals in F9_VALUES.items():
        n, k, t = _reference_threshold(vals)
        assert (thr[cls]["n"], thr[cls]["k"], thr[cls]["threshold"]) == (n, k, t), cls
    assert (thr["Car"]["k"], thr["Car"]["threshold"]) == (3, 1)
    assert thr["Pedestrian"]["threshold"] == 2
    assert thr["Cyclist"]["threshold"] == 5
    assert (thr["Van"]["k"], thr["Van"]["threshold"]) == (2, 1)


def test_f9_threshold_large_random(spark) -> None:
    from kitti import schema

    rng = np.random.default_rng(7)
    rows, ref = [], {}
    for cls, n in (("Car", 997), ("Pedestrian", 31), ("Tram", 1)):
        vals = rng.integers(0, 40, n).tolist()
        ref[cls] = _reference_threshold(vals)
        rows += [_feature_row(i % 5, i + 1, cls, v) for i, v in enumerate(vals)]
    df = spark.createDataFrame(rows, schema.OBJECT_FEATURES)
    got = {r["class"]: (r["n"], r["k"], r["threshold"]) for r in analysis.compute_thresholds(df).collect()}
    assert got == ref


def test_f9_low_list_and_scenes(views: dict, spark) -> None:
    feats = views["object_features"]
    thr_df = analysis.compute_thresholds(feats)
    low = analysis.low_observation(feats, thr_df).toPandas()
    thr = {c: _reference_threshold(v)[2] for c, v in F9_VALUES.items()}
    thr["Truck"] = 50
    exp = sorted(
        (r[2], r[25], r[0], r[1])
        for r in _feature_rows()
        if r[18] and r[25] is not None and r[25] <= thr[r[2]]
    )
    assert list(zip(low["class"], low["points_in_box"], low["frame_id"], low["object_id"])) == exp
    assert (low["points_in_box"] <= low["threshold"]).all()
    scenes = analysis.low_scenes(analysis.low_observation(feats, thr_df)).toPandas()
    assert list(scenes["frame_id"]) == sorted({e[2] for e in exp})
    assert int(scenes["n_low_objects"].sum()) == len(exp)


# --------------------------------------------------------------------------
# 전체 실행 (결과 파일)
# --------------------------------------------------------------------------
def test_run_writes_all_outputs(work: Path, spark, tmp_path: Path) -> None:
    results = tmp_path / "results"
    figures = results / "figures"
    summary = analysis.run(spark, work, results, figures)
    for name in (
        "f5_dataframe_vs_sql.json", "grid_cumulative.csv", "grid_params.json",
        "f7_class_dist.csv", "f7_class_occluded.csv", "f7_class_truncated.csv",
        "f7_class_difficulty.csv", "f9_thresholds.csv", "f9_low_objects.csv",
        "f9_low_scenes.csv", "analysis_summary.json",
    ):
        assert (results / name).is_file(), name
    for name in (
        "f6_grid_cumulative.png", "f6_sample_scenes.png", "f7_class_distance.png",
        "f7_class_occluded.png", "f7_class_truncated.png", "f7_class_difficulty.png",
        "f9_points_distribution.png",
    ):
        assert (figures / name).stat().st_size > 1000, name
    assert summary["f5_pass"] is True
    assert summary["n_frames_processed"] == 3
    assert summary["n_dontcare"] == 2
    cum = pd.read_csv(results / "grid_cumulative.csv")
    assert len(cum) == 6400 and (cum["n_points_mean"] == cum["n_points_sum"] / 3).all()
    params = json.loads((results / "grid_params.json").read_text(encoding="utf-8"))
    assert params["n_frames_processed"] == 3 and params["sample_frames"] == [0, 1, 2]
    assert params["frame_set"] == config.frame_set_info([0, 1, 2, 3])  # frames 표 전체
    assert summary["frame_set"] == params["frame_set"]
    assert summary["parquet_out"] == str(work / "parquet")
    thr_csv = pd.read_csv(results / "f9_thresholds.csv")
    assert list(thr_csv.columns) == ["class", "n", "k", "threshold"]
    low_pq = pd.read_parquet(work / "parquet" / "low_observation")
    assert len(low_pq) == summary["f9"]["n_low_objects"] == len(pd.read_csv(results / "f9_low_objects.csv"))


def test_run_with_frame_subset(work: Path, spark, tmp_path: Path) -> None:
    canonical = work / "parquet"
    before = {n: sorted(p.name for p in (canonical / n).glob("*")) if (canonical / n).exists() else None
              for n in ("grid_stats", "low_observation", "object_features")}
    summary = analysis.run(spark, work, tmp_path / "r", frame_ids=[0, 3])
    assert summary["n_frames"] == 2 and summary["n_frames_processed"] == 1
    cum = pd.read_csv(tmp_path / "r" / "grid_cumulative.csv")
    assert int(cum["n_points_sum"].sum()) == sum(1 for _, c in EDGE_POINTS if c is not None)
    # H2: 파생 Parquet은 <work>/subsets/<name>/ 에, 정본은 그대로
    fs = config.frame_set_info([0, 3])
    out = Path(summary["parquet_out"])
    assert out.parent == work / "subsets" and out.name == f"frames-2-{fs['sha256'][:12]}"
    for name in ("grid_stats", "low_observation", "object_features"):
        assert (out / name).is_dir(), name
    assert set(pd.read_parquet(out / "object_features")["frame_id"]) <= {0, 3}
    after = {n: sorted(p.name for p in (canonical / n).glob("*")) if (canonical / n).exists() else None
             for n in before}
    assert after == before
    assert summary["frame_set"] == fs
    params = json.loads((tmp_path / "r" / "grid_params.json").read_text(encoding="utf-8"))
    assert params["frame_set"] == fs


def test_subset_refuses_canonical_outputs(work: Path, spark, tmp_path: Path) -> None:
    with pytest.raises(analysis.AnalysisRefused, match="results"):
        analysis.run(spark, work, analysis.RESULTS_DIR, frame_ids=[0])
    with pytest.raises(analysis.AnalysisRefused, match="parquet-out"):
        analysis.run(spark, work, tmp_path / "r", frame_ids=[0], parquet_out=work / "parquet")
    assert not (tmp_path / "r").exists()
    lst = tmp_path / "s.txt"
    lst.write_text("0\n", encoding="utf-8")
    assert analysis.main(["--work", str(work), "--frames-file", str(lst)]) == 2  # 기본 --results
    named = analysis.resolve_outputs(work, tmp_path / "r", [0], subset_name="s")
    assert named == work / "subsets" / "s"
