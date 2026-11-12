"""F-11 시험: 합성 KITTI 트리에서 Spark(ingest→features→analysis)와 순차 처리 결과를 대조한다.

- 같은 장면 목록에서 verify가 모든 항목 통과
- 범주별 손상(정수 값, 허용 오차 밖 실수, NULL 위치, 키 누락, 키 중복, 목록 누락)을 각각 실패로 보고
- 허용 오차 안의 실수 흔들림은 통과
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from PIL import Image

from conftest import CALIB_000000
from kitti import analysis, config, ingest, sequential, verify
from kitti import features as kf

PROJECT_DIR = Path(__file__).resolve().parents[1]
CLASSES = ("Car", "Car", "Car", "Pedestrian", "Cyclist", "Van", "Truck")
FRAME_IDS = [0, 1, 2, 3, 4, 5, 6]
EMPTY_LABEL_FRAME = 5  # 객체 없음, .bin 있음
NO_VELO_FRAME = 6  # 객체 없음, .bin 없음 → 정상 처리 프레임에서 빠짐


def _label_lines(rng: np.random.Generator, n: int) -> list[str]:
    lines = []
    for _ in range(n):
        cls = CLASSES[rng.integers(len(CLASSES))]
        trunc = (0.0, 0.0, 0.1, 0.2, 0.4, 0.9)[rng.integers(6)]
        occ = int(rng.integers(4))
        left, top = rng.uniform(0, 1000), rng.uniform(0, 300)
        right, bottom = left + rng.uniform(5, 200), top + rng.uniform(10, 70)
        h, w, l = rng.uniform(1.4, 3.0), rng.uniform(0.5, 2.0), rng.uniform(0.6, 5.0)
        x, z = rng.uniform(-8, 8), rng.uniform(4, 45)
        ry = rng.uniform(-3.1, 3.1)
        lines.append(
            f"{cls} {trunc:.2f} {occ} {rng.uniform(-3, 3):.2f} {left:.2f} {top:.2f} {right:.2f} {bottom:.2f} "
            f"{h:.2f} {w:.2f} {l:.2f} {x:.2f} 1.65 {z:.2f} {ry:.2f}"
        )
    lines.append("DontCare -1 -1 -10 503.89 169.71 590.61 190.13 -1 -1 -1 -1000 -1000 -1000 -10")
    lines.append("Car 0.00 0 0.00 10.00 10.00 50.00 40.00 1.50 1.60 4.00 1.00 1.50 -5.00 0.00")  # z<0: 무효 3D
    return lines


def _points(rng: np.random.Generator, n: int) -> np.ndarray:
    pts = np.column_stack(
        [rng.uniform(-5, 50, n), rng.uniform(-12, 12, n), rng.uniform(-1.9, 0.5, n), rng.uniform(0, 1, n)]
    ).astype(np.float32)
    pts[:3] = [[np.nan, 1.0, 0.0, 0.5], [85.0, 0.0, 0.0, 0.5], [10.0, 45.0, 0.0, 0.5]]  # ROI·NaN 필터 사례
    return pts


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("kitti_f11")
    base = root / "training"
    for sub in config.SUBDIRS:
        (base / sub).mkdir(parents=True)
    rng = np.random.default_rng(11)
    for fid in FRAME_IDS:
        name = f"{fid:06d}"
        Image.new("RGB", (1242, 375)).save(base / "image_2" / f"{name}.png")
        (base / "calib" / f"{name}.txt").write_text(CALIB_000000, encoding="utf-8")
        lines = [] if fid in (EMPTY_LABEL_FRAME, NO_VELO_FRAME) else _label_lines(rng, 12)
        (base / "label_2" / f"{name}.txt").write_text("".join(f"{s}\n" for s in lines), encoding="utf-8")
        if fid != NO_VELO_FRAME:
            _points(rng, 30000).tofile(base / "velodyne" / f"{name}.bin")
    return root


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    config.KITTI_WORK = tmp_path_factory.mktemp("sparkwork_f11")
    session = config.get_spark("local[2]", app="kitti-f11-test")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def runs(tree: Path, spark, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Spark 전체 경로와 순차 경로를 같은 장면에 실행한 결과 폴더."""
    work = tmp_path_factory.mktemp("work_f11")
    results = tmp_path_factory.mktemp("results_f11")
    # FRAME_IDS가 트리 전체이므로 전체 적재(정본). 장면 6은 .bin이 없어 불완전 → 명시적으로 허용.
    ingest.run(tree, work, master="local[2]", n_partitions=3,
               results_dir=results, spark=spark, allow_incomplete=True)
    kf.compute_object_features(spark, FRAME_IDS, tree, work / "parquet" / "object_features", n_partitions=4)
    analysis.run(spark, work, results, results / "figures")
    seq = tmp_path_factory.mktemp("seq_f11")
    sequential.run(FRAME_IDS, tree, seq)
    return {"work": work, "results": results, "seq": seq}


def _verify(runs: dict[str, Path], seq: Path | None = None) -> dict:
    return verify.verify(runs["work"], runs["results"], seq or runs["seq"])


def _item(rep: dict, name: str) -> dict:
    return next(i for i in rep["items"] if i["name"] == name)


# --------------------------------------------------------------------------
# 순차 구현
# --------------------------------------------------------------------------
def test_import_does_not_load_pyspark() -> None:
    code = "import kitti.sequential, kitti.verify, sys; assert 'pyspark' not in sys.modules, 'pyspark loaded'"
    subprocess.run([sys.executable, "-c", code], cwd=PROJECT_DIR, check=True)


def test_sequential_outputs(runs: dict[str, Path]) -> None:
    seq = runs["seq"]
    info = json.loads((seq / "timings.json").read_text(encoding="utf-8"))
    assert info["n_frames"] == len(FRAME_IDS)
    assert info["n_frames_processed"] == len(FRAME_IDS) - 1
    assert info["unprocessed_frames"] == [NO_VELO_FRAME]
    assert set(info["timings_s"]) == {"read", "compute", "write"}
    assert info["blas_threads"] == {v: "1" for v in config.BLAS_THREAD_VARS}
    # 프레임 하나 = row group 하나 (객체 없는 장면은 row group 없음)
    assert pq.ParquetFile(seq / "object_features" / sequential.PART_FILE).num_row_groups == len(FRAME_IDS) - 2
    assert pq.ParquetFile(seq / "grid_stats" / sequential.PART_FILE).num_row_groups == len(FRAME_IDS) - 1
    assert pq.read_schema(seq / "grid_stats" / sequential.PART_FILE).remove_metadata() == sequential.GRID_ARROW_SCHEMA
    for name in ("f5_per_class.json", "grid_cumulative.csv", "grid_params.json", "f7_class_dist.csv",
                 "f7_class_occluded.csv", "f7_class_truncated.csv", "f7_class_difficulty.csv",
                 "f9_thresholds.csv", "f9_low_objects.csv", "f9_low_scenes.csv"):
        assert (seq / name).is_file(), name
    # 같은 열 이름·순서(analysis.py 결과 파일과 같은 형식)
    for name in ("grid_cumulative.csv", "f7_class_dist.csv", "f7_class_occluded.csv", "f7_class_truncated.csv",
                 "f7_class_difficulty.csv", "f9_thresholds.csv", "f9_low_objects.csv", "f9_low_scenes.csv"):
        assert list(pd.read_csv(seq / name).columns) == list(pd.read_csv(runs["results"] / name).columns), name
    assert int(pd.read_csv(seq / "f9_low_objects.csv").shape[0]) > 0


def test_decimal_mean_matches_spark_exactly(runs: dict[str, Path]) -> None:
    spark_rows = json.loads((runs["results"] / "f5_dataframe_vs_sql.json").read_text(encoding="utf-8"))
    seq_rows = json.loads((runs["seq"] / "f5_per_class.json").read_text(encoding="utf-8"))
    a = {r["class"]: r["mean_dist_m"] for r in spark_rows["per_class_rows"]}
    b = {r["class"]: r["mean_dist_m"] for r in seq_rows["per_class_rows"]}
    assert a == b  # 십진 합·반올림 규칙까지 같아 비트 단위로 일치
    assert a[config.DONTCARE] is None


def test_spark_decimal_mean_rules() -> None:
    assert sequential.spark_decimal_mean([]) is None
    assert sequential.spark_decimal_mean([0.1, 0.2]) == 0.15
    # DECIMAL(20,12) 반올림(HALF_UP): 1e-13 단위는 사라진다
    assert sequential.spark_decimal_mean([1.0000000000004, 1.0]) == 1.0
    assert sequential.spark_decimal_mean([1.0000000000005]) == 1.000000000001


def test_trunc_bucket_matches_sql_case() -> None:
    got = sequential.trunc_bucket(np.array([-1.0, np.nan, 0.0, 0.1, 0.15, 0.3, 0.5, 0.51, 1.0]))
    assert got.tolist() == ["invalid", "invalid", "0", "(0,0.15]", "(0,0.15]", "(0.15,0.30]",
                            "(0.30,0.50]", ">0.50", ">0.50"]


def test_sequential_cli(tree: Path, tmp_path: Path) -> None:
    frames = tmp_path / "frames.txt"
    frames.write_text("# 표본\n3\n1\n", encoding="utf-8")
    out = tmp_path / "seq_cli"
    subprocess.run(
        [sys.executable, "-m", "kitti.sequential", "--root", str(tree), "--frames-file", str(frames),
         "--out", str(out)],
        cwd=PROJECT_DIR, check=True, capture_output=True,
    )
    info = json.loads((out / "timings.json").read_text(encoding="utf-8"))
    assert info["n_frames"] == 2 and info["n_frames_processed"] == 2
    assert sorted(set(pq.read_table(out / "grid_stats").column("frame_id").to_pylist())) == [1, 3]


# --------------------------------------------------------------------------
# 대조
# --------------------------------------------------------------------------
def test_verify_passes_on_same_frames(runs: dict[str, Path]) -> None:
    rep = _verify(runs)
    assert rep["pass"], [(i["name"], i.get("error"), i.get("columns")) for i in rep["items"] if not i["pass"]]
    assert {i["name"] for i in rep["items"]} == {"frame_set"} | {s["name"] for s in verify.ITEMS}
    assert rep["frame_set"]["spark"] == rep["frame_set"]["seq"] == config.frame_set_info(FRAME_IDS)
    of = _item(rep, "object_features")
    assert of["rows_spark"] == of["rows_seq"] == of["rows_compared"] > 0
    assert _item(rep, "grid_stats")["rows_compared"] > 0
    assert _item(rep, "f9_low_objects")["rows_compared"] > 0


def test_verify_frame_filter_and_cli(runs: dict[str, Path], tree: Path, tmp_path: Path) -> None:
    frames = tmp_path / "frames.txt"
    frames.write_text("\n".join(str(f) for f in FRAME_IDS) + "\n", encoding="utf-8")
    out = tmp_path / "rep"
    proc = subprocess.run(
        [sys.executable, "-m", "kitti.verify", "--spark-work", str(runs["work"]),
         "--spark-results", str(runs["results"]), "--seq", str(runs["seq"]), "--out", str(out),
         "--frames-file", str(frames)],
        cwd=PROJECT_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rep = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    assert rep["pass"] and rep["frames"] == len(FRAME_IDS)
    md = (out / "verification.md").read_text(encoding="utf-8")
    assert "종합 결과: 통과" in md
    units = (out / "units.md").read_text(encoding="utf-8")
    for spec in verify.ITEMS:
        for name, *_ in spec["fields"]:
            assert f"`{name}`" in units
    # 목록이 양쪽 결과의 장면 집합과 다르면 부분 필터링 없이 frame_set 항목에서 실패(종료 코드 1)
    frames.write_text("0\n1\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kitti.verify", "--spark-work", str(runs["work"]),
         "--spark-results", str(runs["results"]), "--seq", str(runs["seq"]), "--out", str(out),
         "--frames-file", str(frames)],
        cwd=PROJECT_DIR, capture_output=True, text=True,
    )
    assert proc.returncode == 1
    rep = json.loads((out / "verification.json").read_text(encoding="utf-8"))
    fs = _item(rep, "frame_set")
    assert not fs["pass"] and "frame sets differ" in fs["error"]
    assert _item(rep, "object_features")["error"] == "not compared: frame_set check failed"
    assert "frame sets differ" in (out / "verification.md").read_text(encoding="utf-8")


@pytest.fixture
def seq_copy(runs: dict[str, Path], tmp_path: Path) -> Path:
    dst = tmp_path / "seq"
    shutil.copytree(runs["seq"], dst)
    return dst


def _rewrite(path: Path, fn) -> None:
    table = pq.read_table(path)
    out = fn(table)
    pq.write_table(out.cast(table.schema), path)


def _set(table: pa.Table, col: str, row: int, value) -> pa.Table:
    vals = table.column(col).to_pylist()
    vals[row] = value
    idx = table.schema.get_field_index(col)
    return table.set_column(idx, table.schema.field(idx), pa.array(vals, table.schema.field(idx).type))


def _first_row(table: pa.Table, col: str) -> int:
    return int(np.flatnonzero(pc.is_valid(table.column(col)).to_numpy(zero_copy_only=False))[0])


def _features_path(seq: Path) -> Path:
    return seq / "object_features" / sequential.PART_FILE


def test_detects_int_mismatch(runs: dict[str, Path], seq_copy: Path) -> None:
    def bump(t: pa.Table) -> pa.Table:
        r = _first_row(t, "points_in_box")
        return _set(t, "points_in_box", r, t.column("points_in_box")[r].as_py() + 1)

    _rewrite(_features_path(seq_copy), bump)
    _rewrite(seq_copy / "grid_stats" / sequential.PART_FILE,
             lambda t: _set(t, "n_points", 0, t.column("n_points")[0].as_py() + 1))
    rep = _verify(runs, seq_copy)
    assert not rep["pass"]
    of = _item(rep, "object_features")
    assert not of["pass"] and of["columns"]["points_in_box"]["value_mismatch"] == 1
    assert of["value_mismatch"] == 1 and of["null_mismatch"] == 0
    assert of["columns"]["points_in_box"]["examples"][0]["seq"] == of["columns"]["points_in_box"]["examples"][0]["spark"] + 1
    gs = _item(rep, "grid_stats")
    assert gs["columns"]["n_points"]["value_mismatch"] == 1


def test_detects_float_beyond_tolerance(runs: dict[str, Path], seq_copy: Path) -> None:
    def shift(t: pa.Table) -> pa.Table:
        r = _first_row(t, "dist_m")
        v = t.column("dist_m")[r].as_py()
        return _set(t, "dist_m", r, v + 1e-6 + 2e-6 * abs(v))

    _rewrite(_features_path(seq_copy), shift)
    rep = _verify(runs, seq_copy)
    col = _item(rep, "object_features")["columns"]["dist_m"]
    assert col["value_mismatch"] == 1 and col["null_mismatch"] == 0
    assert "object_features" in rep["failed_items"]


def test_float_within_tolerance_passes(runs: dict[str, Path], seq_copy: Path) -> None:
    def jitter(t: pa.Table) -> pa.Table:
        d = t.column("dist_m")
        return t.set_column(t.schema.get_field_index("dist_m"), t.schema.field("dist_m"),
                            pc.multiply(d, 1.0 + 5e-7))

    _rewrite(_features_path(seq_copy), jitter)
    path = seq_copy / "grid_cumulative.csv"
    cum = pd.read_csv(path)
    cum["n_points_mean"] = cum["n_points_mean"] * (1.0 + 5e-7) + 5e-7
    cum.to_csv(path, index=False)
    rep = _verify(runs, seq_copy)
    assert rep["pass"], rep["failed_items"]
    assert _item(rep, "object_features")["columns"]["dist_m"]["max_abs_diff"] > 0


def test_detects_null_position(runs: dict[str, Path], seq_copy: Path) -> None:
    _rewrite(_features_path(seq_copy), lambda t: _set(t, "points_in_box", _first_row(t, "points_in_box"), None))
    rep = _verify(runs, seq_copy)
    col = _item(rep, "object_features")["columns"]["points_in_box"]
    assert col["null_mismatch"] == 1 and col["value_mismatch"] == 0
    assert col["examples"][0]["seq"] is None
    assert not rep["pass"]


def test_detects_missing_key(runs: dict[str, Path], seq_copy: Path) -> None:
    _rewrite(_features_path(seq_copy), lambda t: t.slice(1))
    rep = _verify(runs, seq_copy)
    of = _item(rep, "object_features")
    assert of["keys_only_in_spark"] == 1 and of["keys_only_in_seq"] == 0
    assert of["missing_key_examples"]["only_in_spark"] == [{"frame_id": 0, "object_id": 1}]
    assert of["null_mismatch"] == of["value_mismatch"] == 0  # 나머지 행은 일치
    assert not of["pass"]


def test_detects_duplicate_key(runs: dict[str, Path], seq_copy: Path) -> None:
    _rewrite(_features_path(seq_copy), lambda t: pa.concat_tables([t, t.slice(2, 1)]))
    rep = _verify(runs, seq_copy)
    of = _item(rep, "object_features")
    assert of["duplicate_keys_seq"] == 1 and of["duplicate_keys_spark"] == 0
    assert of["keys_only_in_spark"] == of["keys_only_in_seq"] == 0
    assert not of["pass"]


def test_detects_csv_list_and_count_changes(runs: dict[str, Path], seq_copy: Path) -> None:
    low = pd.read_csv(seq_copy / "f9_low_objects.csv")
    low.iloc[1:].to_csv(seq_copy / "f9_low_objects.csv", index=False)  # 추출 목록에서 한 객체 누락
    thr = pd.read_csv(seq_copy / "f9_thresholds.csv")
    thr.loc[0, "threshold"] += 1
    thr.to_csv(seq_copy / "f9_thresholds.csv", index=False)
    dist = pd.read_csv(seq_copy / "f7_class_dist.csv")
    dist.loc[0, "n_objects"] += 1
    dist.to_csv(seq_copy / "f7_class_dist.csv", index=False)
    params = json.loads((seq_copy / "grid_params.json").read_text(encoding="utf-8"))
    params["n_frames_processed"] += 1
    (seq_copy / "grid_params.json").write_text(json.dumps(params), encoding="utf-8")
    rep = _verify(runs, seq_copy)
    assert _item(rep, "f9_low_objects")["keys_only_in_spark"] == 1
    assert _item(rep, "f9_thresholds")["columns"]["threshold"]["value_mismatch"] == 1
    assert _item(rep, "f7_class_dist")["columns"]["n_objects"]["value_mismatch"] == 1
    assert _item(rep, "grid_params")["columns"]["value"]["value_mismatch"] == 1
    assert set(rep["failed_items"]) == {"f9_low_objects", "f9_thresholds", "f7_class_dist", "grid_params"}
    md = verify.render_markdown(rep)
    assert "종합 결과: 실패" in md and "불일치 상세" in md


def test_missing_file_reports_error(runs: dict[str, Path], seq_copy: Path) -> None:
    (seq_copy / "f9_low_scenes.csv").unlink()
    rep = _verify(runs, seq_copy)
    item = _item(rep, "f9_low_scenes")
    assert not item["pass"] and "FileNotFoundError" in item["error"]


# --------------------------------------------------------------------------
# M2: 빈 대조·장면 집합
# --------------------------------------------------------------------------
@pytest.fixture
def results_copy(runs: dict[str, Path], tmp_path: Path) -> Path:
    dst = tmp_path / "spark_results"
    shutil.copytree(runs["results"], dst)
    return dst


def _truncate_csv(path: Path) -> None:
    pd.read_csv(path).iloc[0:0].to_csv(path, index=False)


def test_empty_items_fail(runs: dict[str, Path], seq_copy: Path, results_copy: Path) -> None:
    for d in (seq_copy, results_copy):
        _truncate_csv(d / "f7_class_dist.csv")
        _truncate_csv(d / "f9_low_scenes.csv")
    rep = verify.verify(runs["work"], results_copy, seq_copy)
    item = _item(rep, "f7_class_dist")
    assert item["rows_compared"] == 0 and not item["pass"] and "no rows compared" in item["error"]
    # f9_low_*는 f9_thresholds가 1행 이상 대조됐으면 비어 있어도 된다
    assert _item(rep, "f9_low_scenes")["pass"]
    assert set(rep["failed_items"]) == {"f7_class_dist"}
    for d in (seq_copy, results_copy):
        _truncate_csv(d / "f9_thresholds.csv")
    rep = verify.verify(runs["work"], results_copy, seq_copy)
    assert {"f9_thresholds", "f9_low_scenes", "f7_class_dist"} <= set(rep["failed_items"])
    assert "f9_thresholds compared > 0" in _item(rep, "f9_low_scenes")["error"]


def test_frame_set_mismatch_fails(runs: dict[str, Path], seq_copy: Path) -> None:
    path = seq_copy / "grid_params.json"
    params = json.loads(path.read_text(encoding="utf-8"))
    params["frame_set"] = config.frame_set_info([0, 1, 2])
    path.write_text(json.dumps(params), encoding="utf-8")
    rep = verify.verify(runs["work"], runs["results"], seq_copy)
    assert not rep["pass"]
    fs = _item(rep, "frame_set")
    assert "frame sets differ" in fs["error"] and "n=3" in fs["error"] and "n=7" in fs["error"]
    assert all(i.get("error") == "not compared: frame_set check failed" for i in rep["items"][1:])
    del params["frame_set"]
    path.write_text(json.dumps(params), encoding="utf-8")
    rep = verify.verify(runs["work"], runs["results"], seq_copy)
    assert "not recorded" in _item(rep, "frame_set")["error"] and not rep["pass"]


def test_frames_file_must_match_recorded_set(runs: dict[str, Path]) -> None:
    assert verify.verify(runs["work"], runs["results"], runs["seq"], FRAME_IDS)["pass"]
    rep = verify.verify(runs["work"], runs["results"], runs["seq"], FRAME_IDS[:-1])
    assert not rep["pass"] and "frames_file" in _item(rep, "frame_set")["error"]


# --------------------------------------------------------------------------
# M3: 자기 대조 금지
# --------------------------------------------------------------------------
def test_self_comparison_refused(runs: dict[str, Path]) -> None:
    rep = verify.verify(runs["seq"], runs["seq"], runs["seq"])
    assert not rep["pass"]
    specs = {s["name"]: s for s in verify.ITEMS}
    for item in rep["items"][1:]:
        assert not item["pass"], item["name"]
        if specs[item["name"]]["spark"][1] == specs[item["name"]]["seq"][1]:  # f5만 파일 이름이 다르다
            assert "same path" in item["error"], item["name"]


def test_seq_side_reads_only_seq_dir(runs: dict[str, Path], seq_copy: Path) -> None:
    """순차 쪽은 <seq>/<표>만 읽는다: <seq>/parquet/<표>가 있어도 쓰지 않는다."""
    nested = seq_copy / "parquet" / "object_features"
    nested.parent.mkdir()
    shutil.copytree(runs["work"] / "parquet" / "object_features", nested)
    shutil.rmtree(seq_copy / "object_features")
    item = _item(verify.verify(runs["work"], runs["results"], seq_copy), "object_features")
    assert not item["pass"] and "FileNotFoundError" in item["error"]


def test_sequential_refuses_spark_dirs(runs: dict[str, Path], tree: Path) -> None:
    with pytest.raises(ValueError, match="parquet"):
        sequential.run(FRAME_IDS, tree, runs["work"])
    with pytest.raises(ValueError, match="analysis_summary.json"):
        sequential.run(FRAME_IDS, tree, runs["results"])
    assert (runs["work"] / "parquet" / "grid_stats").is_dir()  # 아무것도 지우지 않음


# --------------------------------------------------------------------------
# M4: grid_stats 구간별 대조 = 전체 대조
# --------------------------------------------------------------------------
def _stats(res: dict) -> dict:
    keys = ("rows_spark", "rows_seq", "duplicate_keys_spark", "duplicate_keys_seq", "keys_only_in_spark",
            "keys_only_in_seq", "rows_compared", "null_mismatch", "value_mismatch", "pass",
            "missing_key_examples")
    cols = {n: {k: c[k] for k in ("null_mismatch", "value_mismatch", "max_abs_diff", "examples") if k in c}
            for n, c in res["columns"].items()}
    return {**{k: res[k] for k in keys}, "columns": cols}


@pytest.mark.parametrize("modify", [False, True])
def test_chunked_grid_compare_equals_full(runs: dict[str, Path], seq_copy: Path, modify: bool) -> None:
    spec = next(s for s in verify.ITEMS if s["name"] == "grid_stats")
    fields = spec["fields"]
    a_path = runs["work"] / "parquet" / "grid_stats"
    b_path = seq_copy / "grid_stats"
    if modify:  # 여러 장면에 값·키 불일치를 만든다
        def change(t: pa.Table) -> pa.Table:
            fid = t.column("frame_id").to_numpy()
            rows = [int(np.flatnonzero(fid == f)[0]) for f in (0, 2, 4)]
            for r in rows:
                t = _set(t, "n_points", r, t.column("n_points")[r].as_py() + 1)
            return t.slice(0, t.num_rows - 3)
        _rewrite(b_path / sequential.PART_FILE, change)
    full = verify.compare_tables(verify._normalize(pq.read_table(a_path), fields),
                                 verify._normalize(pq.read_table(b_path), fields), fields)
    for chunk in (1, 2, 3, 10_000):
        got = verify.compare_parquet_chunked(a_path, b_path, fields, chunk_frames=chunk)
        assert _stats(got) == _stats(full), chunk
    assert full["pass"] is (not modify)
    if modify:
        assert full["columns"]["n_points"]["value_mismatch"] == 3 and full["keys_only_in_spark"] == 3


# --------------------------------------------------------------------------
# H2 + 부분집합: analysis 부분집합 폴더를 그대로 verify --spark-work로 대조
# --------------------------------------------------------------------------
def test_subset_analysis_verifies_against_sequential(runs: dict[str, Path], tree: Path, spark,
                                                      tmp_path: Path) -> None:
    subset = [0, 2, 5]
    res = tmp_path / "res"
    summary = analysis.run(spark, runs["work"], res, frame_ids=subset)
    seq = tmp_path / "seq_sub"
    sequential.run(subset, tree, seq)
    rep = verify.verify(summary["parquet_out"], res, seq, subset)
    assert rep["pass"], [(i["name"], i.get("error")) for i in rep["items"] if not i["pass"]]
    assert _item(rep, "object_features")["spark_source"].startswith(str(runs["work"] / "subsets"))
    # 전체 Spark 결과와 부분집합 순차 결과는 장면 집합이 달라 대조되지 않는다
    assert not verify.verify(runs["work"], runs["results"], seq)["pass"]


# --------------------------------------------------------------------------
# 최종 검토 반영: NULL 키, f9_low 최소 행 수, 경로 표기, 다시 그리기
# --------------------------------------------------------------------------
_NK_FIELDS = [("cls", "key", "string", "-", ""), ("k", "key", "int32", "-", ""), ("v", "int", "int64", "-", "")]


def _nk(rows: list[tuple]) -> pa.Table:
    t = pa.table({"cls": pa.array([r[0] for r in rows], pa.string()),
                  "k": pa.array([r[1] for r in rows], pa.int32()),
                  "v": pa.array([r[2] for r in rows], pa.int64())})
    return verify._normalize(t, _NK_FIELDS)


def test_null_join_keys_match_each_other() -> None:
    a = _nk([("Car", 1, 5), (None, 2, 6), ("Van", None, 7), (None, None, 8)])
    b = _nk([(None, None, 8), ("Van", None, 7), (None, 2, 6), ("Car", 1, 5)])
    res = verify.compare_tables(a, b, _NK_FIELDS)
    assert res["keys_only_in_spark"] == res["keys_only_in_seq"] == 0
    assert res["rows_compared"] == 4 and res["value_mismatch"] == 0 and res["pass"]
    # NULL 키 행의 값이 다르면 값 불일치로 잡는다(키 누락으로 잘못 보고하지 않음)
    res = verify.compare_tables(a, _nk([(None, None, 9), ("Van", None, 7), (None, 2, 6), ("Car", 1, 5)]),
                                _NK_FIELDS)
    assert res["keys_only_in_spark"] == 0 and res["value_mismatch"] == 1 and not res["pass"]
    # 한쪽에만 있는 NULL 키는 누락으로 보고하고, 예시에는 표지값이 아니라 NULL이 나온다
    res = verify.compare_tables(a, _nk([("Car", 1, 5), (None, 2, 6), ("Van", None, 7)]), _NK_FIELDS)
    assert res["keys_only_in_spark"] == 1
    assert res["missing_key_examples"]["only_in_spark"] == [{"cls": None, "k": None}]


def test_f9_low_objects_must_cover_every_threshold_class(runs: dict[str, Path], seq_copy: Path,
                                                         results_copy: Path) -> None:
    n_thr = len(pd.read_csv(seq_copy / "f9_thresholds.csv"))
    assert n_thr >= 2
    for d in (seq_copy, results_copy):
        low = pd.read_csv(d / "f9_low_objects.csv")
        low.iloc[: n_thr - 1].to_csv(d / "f9_low_objects.csv", index=False)  # 양쪽 같게 줄임
    rep = verify.verify(runs["work"], results_copy, seq_copy)
    item = _item(rep, "f9_low_objects")
    assert item["rows_compared"] == n_thr - 1 and not item["pass"]
    assert "< f9_thresholds" in item["error"]
    # 양쪽 모두 빈 f9_low_objects도 기준값이 있으면 실패, 그때는 빈 f9_low_scenes 예외도 적용하지 않는다
    for d in (seq_copy, results_copy):
        _truncate_csv(d / "f9_low_objects.csv")
        _truncate_csv(d / "f9_low_scenes.csv")
    rep = verify.verify(runs["work"], results_copy, seq_copy)
    assert {"f9_low_objects", "f9_low_scenes"} <= set(rep["failed_items"])


def test_report_paths_use_tilde_and_render_only(runs: dict[str, Path], tmp_path: Path) -> None:
    rep = _verify(runs)
    home = str(Path.home())
    fake = json.loads(json.dumps(rep).replace(str(runs["work"]), home + "/w"))
    assert verify._display_path(home + "/w/x") == "~/w/x" and verify._display_path("/tmp/x") == "/tmp/x"
    verify.write_reports(rep, tmp_path)
    (tmp_path / "verification.md").unlink()
    (tmp_path / "units.md").unlink()
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    (fake_dir / "verification.json").write_text(json.dumps(fake), encoding="utf-8")
    assert verify.main(["--out", str(tmp_path), "--render-only"]) == 0
    md = (tmp_path / "verification.md").read_text(encoding="utf-8")
    units = (tmp_path / "units.md").read_text(encoding="utf-8")
    assert verify.SHARED_FUNCTION_NOTE in md and verify.SHARED_FUNCTION_NOTE in units
    assert "f8_independent_check.json" in md and "features_from_arrays" in units
    with pytest.raises(SystemExit):
        verify.main(["--out", str(tmp_path)])  # 대조 모드에는 입력 폴더가 필요하다


def test_verify_writes_home_paths_with_tilde(runs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: runs["work"].parent))
    rep = _verify(runs)
    text = json.dumps(rep)
    assert str(runs["work"].parent) + "/" not in text
    assert rep["spark_work"] == "~/" + runs["work"].name
    assert _item(rep, "object_features")["spark_source"].startswith("~/")
