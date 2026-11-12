"""object_features(F-7·F-8) 시험: 합성 장면으로 손계산 값·NULL 정책·Spark==순차 대조."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from kitti import config
from kitti import features as kf

PROJECT_DIR = Path(__file__).resolve().parents[1]

# R0_rect = I, Tr_velo_to_cam = 순수 축 교환(rect x = -velo y, rect y = -velo z, rect z = velo x).
# 0/±1 계수뿐이라 변환이 부동소수 오차 없이 정확하다.
CALIB_SYNTH = """\
P0: 1 0 0 0 0 1 0 0 0 0 1 0
P1: 1 0 0 0 0 1 0 0 0 0 1 0
P2: 1 0 0 0 0 1 0 0 0 0 1 0
P3: 1 0 0 0 0 1 0 0 0 0 1 0
R0_rect: 1 0 0 0 1 0 0 0 1
Tr_velo_to_cam: 0 -1 0 0 0 0 -1 0 1 0 0 0
Tr_imu_to_velo: 1 0 0 0 0 1 0 0 0 0 1 0
"""

# object_id 1 Car(유효, ry=0) / 2 Pedestrian(유효, ry=π/2) / 3 DontCare / 4 Car(z<0, 무효) /
# 5 Cyclist(유효, 포인트 0개, 거리 정확히 40 m, Hard 경계값)
LABEL_SYNTH = """\
Car 0.00 0 0.0 100.0 100.0 200.0 150.0 1.5 1.6 4.0 2.0 1.5 10.0 0.0
Pedestrian 0.20 1 0.0 300.0 200.0 320.0 230.0 1.8 0.6 0.8 -3.0 1.6 8.0 1.5707963267948966
DontCare -1 -1 -10 503.89 169.71 590.61 190.13 -1 -1 -1 -1000 -1000 -1000 -10
Car 0.40 2 0.0 10.0 10.0 50.0 30.0 1.5 1.6 4.0 1.0 1.5 -5.0 0.0
Cyclist 0.50 2 0.0 400.0 100.0 420.0 125.0 1.7 0.6 1.8 0.0 1.5 40.0 0.0
"""


def _f32_up(value: float, ulps: int) -> np.float32:
    """value(float32)에서 위로 ulps 칸 떨어진 float32."""
    v = np.float32(value)
    for _ in range(ulps):
        v = np.nextafter(v, np.float32(np.inf))
    return v


def _rect_points() -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """(Car 내부, 그 밖의) rect 좌표 점. Car: x∈[0,4], y∈[0,1.5], z∈[9.2,10.8] (±ε)."""
    four_in = float(_f32_up(4.0, 2))  # 4 + 9.5e-7 ≤ 4 + 1e-6 → 경계 여유 안
    four_out = float(_f32_up(4.0, 4))  # 4 + 1.9e-6 > 4 + 1e-6 → 밖
    ground_in = 1.5 + 2.0**-20  # dy = 9.5e-7 ≤ ε
    ground_out = 1.5 + 2.0**-19  # dy = 1.9e-6 > ε
    car_in = [
        (2.0, 0.75, 10.0),  # 중심
        (four_in, 0.75, 10.0),  # lx 경계 + 여유
        (2.0, 1.5, 10.0),  # 바닥면(경계 포함)
        (2.0, ground_in, 10.0),  # 바닥면 + 여유
        (2.0, 0.0, 10.0),  # 윗면(경계 포함)
        (2.0, 0.75, 10.8),  # lz 경계(float32 10.8 = 10.8000002)
    ]
    others = [
        (four_out, 0.75, 10.0),  # lx 경계 + 2ε → Car 밖
        (2.0, ground_out, 10.0),  # 바닥 아래 → 밖
        (2.0, -0.01, 10.0),  # 윗면 위 → 밖
        (2.0, 0.75, 10.81),  # lz 밖
        # Pedestrian (x=-3, z=8, ry=π/2): 회전 후 |dz| ≤ 0.4, |dx| ≤ 0.3
        (-3.0, 0.7, 8.0),  # 중심 → 안
        (-3.0, 0.7, 8.375),  # dz=0.375 → 회전했으므로 안 (회전 무시면 밖)
        (-2.625, 0.7, 8.0),  # dx=0.375 → 회전했으므로 밖 (회전 무시면 안)
        # 카메라 뒤 Car(z=-5) 상자 자리의 점: 무효 3D라 세지 않는다
        (1.0, 1.0, -5.0),
        (1.0, 0.5, -5.2),
    ]
    return car_in, others


def _write_frame(root: Path, frame_id: int, label: str, rect_pts: list) -> None:
    train = root / "training"
    for sub in ("label_2", "calib", "velodyne"):
        (train / sub).mkdir(parents=True, exist_ok=True)
    (train / "label_2" / f"{frame_id:06d}.txt").write_text(label, encoding="utf-8")
    (train / "calib" / f"{frame_id:06d}.txt").write_text(CALIB_SYNTH, encoding="utf-8")
    # rect (xr, yr, zr) → velo (zr, -xr, -yr)
    velo = np.array([[zr, -xr, -yr, 0.5] for xr, yr, zr in rect_pts], dtype=np.float32)
    velo.reshape(-1, 4).tofile(train / "velodyne" / f"{frame_id:06d}.bin")


@pytest.fixture
def synth_root(tmp_path: Path) -> Path:
    root = tmp_path / "kitti"
    car_in, others = _rect_points()
    _write_frame(root, 7, LABEL_SYNTH, car_in + others)
    # 장면 3: DontCare만 / 장면 12: 빈 라벨 / 장면 20: 같은 라벨, 점 순서 반대
    _write_frame(root, 3, LABEL_SYNTH.splitlines()[2] + "\n", car_in)
    _write_frame(root, 12, "", car_in)
    _write_frame(root, 20, LABEL_SYNTH, list(reversed(car_in + others)))
    return root


def _rows(table) -> dict[int, dict]:
    return {r["object_id"]: r for r in table.to_pylist()}


def test_points_in_box_exact_counts(synth_root: Path) -> None:
    rows = _rows(kf.frame_features(7, synth_root))
    assert [rows[i]["points_in_box"] for i in range(1, 6)] == [6, 2, None, None, 0]


def test_null_policies_and_validity(synth_root: Path) -> None:
    rows = _rows(kf.frame_features(7, synth_root))
    assert sorted(rows) == [1, 2, 3, 4, 5]  # DontCare 포함 모든 줄
    dc = rows[3]
    assert dc["is_dontcare"] and not dc["valid_3d"]
    assert dc["dist_m"] is None and dc["dist_bin"] is None and dc["points_in_box"] is None
    assert (dc["eligible_easy"], dc["eligible_moderate"], dc["eligible_hard"]) == (False,) * 3
    assert dc["diff_level"] == config.DIFF_NONE
    assert dc["bbox_height"] == 190.13 - 169.71
    # 원본 무효 표식 보존
    assert (dc["x"], dc["h"], dc["ry"], dc["occluded"], dc["truncated"]) == (-1000.0, -1.0, -10.0, -1, -1.0)

    behind = rows[4]
    assert not behind["is_dontcare"] and not behind["valid_3d"]
    assert behind["points_in_box"] is None
    assert behind["dist_m"] == math.sqrt(1.0 * 1.0 + (-5.0) * (-5.0))  # 일반 객체는 거리 계산
    assert behind["dist_bin"] == "0-10"

    assert all(rows[i]["valid_3d"] for i in (1, 2, 5))
    assert rows[5]["points_in_box"] == 0  # 유효 박스의 0은 NULL과 구분


def test_distance_and_bins(synth_root: Path) -> None:
    rows = _rows(kf.frame_features(7, synth_root))
    assert rows[1]["dist_m"] == math.sqrt(2.0 * 2.0 + 10.0 * 10.0)
    assert rows[1]["dist_bin"] == "10-20"
    assert rows[2]["dist_m"] == math.sqrt(9.0 + 64.0)
    assert rows[2]["dist_bin"] == "0-10"
    assert rows[5]["dist_m"] == 40.0
    assert rows[5]["dist_bin"] == "40+"  # 경계 40은 위 구간


def test_eligibility(synth_root: Path) -> None:
    rows = _rows(kf.frame_features(7, synth_root))

    def flags(i: int) -> tuple:
        r = rows[i]
        return r["bbox_height"], r["eligible_easy"], r["eligible_moderate"], r["eligible_hard"], r["diff_level"]

    assert flags(1) == (50.0, True, True, True, 1)  # Easy
    assert flags(2) == (30.0, False, True, True, 2)  # occ 1, trunc 0.2
    assert flags(4) == (20.0, False, False, False, 4)  # 높이 20 < 25
    assert flags(5) == (25.0, False, False, True, 3)  # 25 px, occ 2, trunc 0.50 모두 Hard 경계
    for r in rows.values():
        assert r["eligible_moderate"] == (r["diff_level"] <= 2)


def test_empty_and_dontcare_only_frames(synth_root: Path) -> None:
    assert kf.frame_features(12, synth_root).num_rows == 0
    only = kf.frame_features(3, synth_root).to_pylist()
    assert len(only) == 1 and only[0]["object_id"] == 1 and only[0]["is_dontcare"]
    assert only[0]["points_in_box"] is None


def test_point_order_does_not_change_result(synth_root: Path) -> None:
    a = kf.frame_features(7, synth_root).drop_columns(["frame_id"])
    b = kf.frame_features(20, synth_root).drop_columns(["frame_id"])
    assert a.equals(b)


def test_arrow_schema_matches_spark_schema() -> None:
    from pyspark.sql.types import BooleanType, DoubleType, IntegerType, StringType
    import pyarrow as pa

    from kitti import schema

    to_arrow = {
        IntegerType(): pa.int32(),
        DoubleType(): pa.float64(),
        StringType(): pa.string(),
        BooleanType(): pa.bool_(),
    }
    spark_fields = [(f.name, to_arrow[f.dataType], f.nullable) for f in schema.OBJECT_FEATURES]
    arrow_fields = [(f.name, f.type, f.nullable) for f in kf.ARROW_SCHEMA]
    assert arrow_fields == spark_fields


def test_import_does_not_load_pyspark() -> None:
    code = "import kitti.features, sys; assert 'pyspark' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], cwd=PROJECT_DIR, check=True)


def test_read_frame_ids(tmp_path: Path) -> None:
    path = tmp_path / "frames.txt"
    path.write_text("12\n3 7  # 주석 9\n\n7\n", encoding="utf-8")
    assert kf.read_frame_ids(path) == [3, 7, 12]


def test_sequential_writes_parquet_idempotently(synth_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "seq"
    assert kf.compute_object_features_sequential([20, 7, 12, 3], synth_root, out) == 11
    assert kf.compute_object_features_sequential([20, 7, 12, 3], synth_root, out) == 11
    table = pq.read_table(out)
    assert table.num_rows == 11
    assert table.schema.remove_metadata() == kf.ARROW_SCHEMA
    assert pq.ParquetFile(out / kf.SEQUENTIAL_FILE).num_row_groups == 3  # 빈 장면은 row group 없음


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    os.environ["KITTI_WORK"] = str(tmp_path_factory.mktemp("work"))
    config.KITTI_WORK = Path(os.environ["KITTI_WORK"])
    session = config.get_spark("local[2]", app="kitti-features-test")
    yield session
    session.stop()


def _sorted(table):
    return table.sort_by([("frame_id", "ascending"), ("object_id", "ascending")])


def test_spark_equals_sequential(spark, synth_root: Path, tmp_path: Path) -> None:
    from kitti import schema

    ids = [20, 7, 12, 3]
    seq_out, spark_out = tmp_path / "seq", tmp_path / "spark"
    n_seq = kf.compute_object_features_sequential(ids, synth_root, seq_out)
    n_spark = kf.compute_object_features(spark, ids, synth_root, spark_out, n_partitions=32)
    assert n_seq == n_spark == 11
    # 두 번 실행해도 덮어쓰기(멱등)
    assert kf.compute_object_features(spark, ids, synth_root, spark_out, n_partitions=3) == 11

    seq_t = _sorted(pq.read_table(seq_out))
    spark_t = _sorted(pq.read_table(spark_out))
    assert [f.name for f in spark_t.schema] == [f.name for f in seq_t.schema]
    assert [f.type for f in spark_t.schema] == [f.type for f in seq_t.schema]
    assert spark_t.to_pylist() == seq_t.to_pylist()  # 실수도 == 로 정확히 비교

    df = spark.read.parquet(str(spark_out))
    assert [(f.name, f.dataType) for f in df.schema] == [
        (f.name, f.dataType) for f in schema.OBJECT_FEATURES
    ]
    assert all("comment" in f.metadata for f in df.schema)
    seq_df = spark.read.parquet(str(seq_out))
    assert [(f.name, f.dataType) for f in seq_df.schema] == [
        (f.name, f.dataType) for f in schema.OBJECT_FEATURES
    ]
    assert df.exceptAll(seq_df.select(df.columns)).count() == 0
    assert seq_df.select(df.columns).exceptAll(df).count() == 0


# --------------------------------------------------------------------------
# L2: .bin 없음·크기 오류 정책 (Spark·순차 공통)
# --------------------------------------------------------------------------
@pytest.fixture
def bad_bin_root(synth_root: Path) -> Path:
    """synth_root + 장면 30(.bin 없음), 31(크기 50바이트), 32(.bin 없음, 빈 라벨: 기록 대상 아님)."""
    for fid in (30, 31, 32):
        _write_frame(synth_root, fid, LABEL_SYNTH if fid != 32 else "", [])
    velo = synth_root / "training" / "velodyne"
    (velo / "000030.bin").unlink()
    (velo / "000031.bin").write_bytes(b"\0" * 50)
    (velo / "000032.bin").unlink()
    return synth_root


def test_bad_bin_gives_null_points_in_box(bad_bin_root: Path) -> None:
    assert kf.velodyne_problem(bad_bin_root, 30) == "missing"
    assert kf.velodyne_problem(bad_bin_root, 31).startswith("bad_size")
    assert kf.velodyne_problem(bad_bin_root, 7) is None
    for fid in (30, 31):
        rows = _rows(kf.frame_features(fid, bad_bin_root))
        assert sorted(rows) == [1, 2, 3, 4, 5]  # 객체 행은 모두 남는다
        assert all(r["points_in_box"] is None for r in rows.values())
        assert rows[1]["valid_3d"] and rows[1]["dist_m"] == pytest.approx(math.hypot(2.0, 10.0))


def test_bad_bin_sequential_sidecar(bad_bin_root: Path, tmp_path: Path) -> None:
    import json

    out = tmp_path / "seq"
    n = kf.compute_object_features_sequential([7, 30, 31, 32], bad_bin_root, out)
    assert n == 15
    doc = json.loads((out / kf.SKIPPED_FILE).read_text(encoding="utf-8"))
    assert [f["frame_id"] for f in doc["frames"]] == [30, 31] and doc["count"] == 2
    assert doc["frames"][0]["reason"] == "missing"
    assert pq.read_table(out).num_rows == 15  # 사이드카(_로 시작)는 Parquet 읽기에서 무시


def test_bad_bin_spark_matches_sequential(spark, bad_bin_root: Path, tmp_path: Path) -> None:
    ids = [7, 30, 31, 32]
    seq_out, spark_out = tmp_path / "seq", tmp_path / "spark"
    assert kf.compute_object_features_sequential(ids, bad_bin_root, seq_out) == 15
    assert kf.compute_object_features(spark, ids, bad_bin_root, spark_out, n_partitions=2) == 15
    assert _sorted(pq.read_table(spark_out)).to_pylist() == _sorted(pq.read_table(seq_out)).to_pylist()
    assert (spark_out / kf.SKIPPED_FILE).read_text(encoding="utf-8") == \
        (seq_out / kf.SKIPPED_FILE).read_text(encoding="utf-8")
    assert spark.read.parquet(str(spark_out)).count() == 15


# --------------------------------------------------------------------------
# L5: CLI --work, 기본 출력 경로는 실행 시점에 정한다
# --------------------------------------------------------------------------
def test_default_out_is_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert not hasattr(kf, "DEFAULT_OUT")
    monkeypatch.setattr(config, "KITTI_WORK", tmp_path / "w1")
    assert kf.default_out() == tmp_path / "w1" / "parquet" / "object_features"
    assert kf.default_out(tmp_path / "w2") == tmp_path / "w2" / "parquet" / "object_features"


def test_cli_work_option(synth_root: Path, tmp_path: Path) -> None:
    frames = tmp_path / "f.txt"
    frames.write_text("7\n3\n", encoding="utf-8")
    work = tmp_path / "work"
    subprocess.run(
        [sys.executable, "-m", "kitti.features", "--mode", "sequential", "--work", str(work),
         "--root", str(synth_root), "--frames-file", str(frames)],
        cwd=PROJECT_DIR, check=True, capture_output=True,
    )
    assert pq.read_table(work / "parquet" / "object_features").num_rows == 6


# --------------------------------------------------------------------------
# L6: 독립 점검 스크립트 (devkit 꼭짓점 + 면 반공간)
# --------------------------------------------------------------------------
def _load_checker():
    import importlib.util

    path = PROJECT_DIR / "scripts" / "check_box_independent.py"
    spec = importlib.util.spec_from_file_location("check_box_independent", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_independent_checker_agrees(synth_root: Path) -> None:
    from kitti import geometry as g

    chk = _load_checker()
    for fid in (7, 20, 3, 12):
        assert chk.check_frame(fid, str(synth_root)) == [], fid
    rng = np.random.default_rng(5)
    pts = rng.uniform(-6, 6, (20000, 3)) + np.array([0.0, 0.0, 15.0])
    objs = [
        {"class": "Car", "h": rng.uniform(1, 3), "w": rng.uniform(1, 3), "l": rng.uniform(2, 6),
         "x": rng.uniform(-2, 2), "y": rng.uniform(0, 2), "z": rng.uniform(13, 17), "ry": rng.uniform(-3.1, 3.1)}
        for _ in range(30)
    ]
    assert chk.independent_counts(pts, objs) == g.points_in_box_counts(pts, objs)
    assert sum(chk.independent_counts(pts, objs)) > 0


def test_independent_checker_fails_without_bin(bad_bin_root: Path) -> None:
    chk = _load_checker()
    assert chk.check_frame(30, str(bad_bin_root)) == ["frame 30: velodyne missing; cannot check"]
    assert chk.main(["--root", str(bad_bin_root), "7", "30"]) == 1
    assert chk.main(["--root", str(bad_bin_root), "7", "20"]) == 0


def test_independent_checker_frames_file_and_json(bad_bin_root: Path, tmp_path: Path) -> None:
    chk = _load_checker()
    frames = tmp_path / "frames.txt"
    frames.write_text("# sample\n20\n7\n", encoding="utf-8")
    out = tmp_path / "out" / "f8_independent_check.json"
    assert chk.main(["--root", str(bad_bin_root), "--frames-file", str(frames), "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["pass"] is True and rep["n_frames"] == 2 and rep["n_mismatches"] == 0
    assert [f["frame_id"] for f in rep["frames"]] == [7, 20]
    assert rep["n_valid_3d_compared"] == sum(f["n_valid_3d"] for f in rep["frames"]) > 0
    assert "points_in_box_mask is not used" in rep["method"]
    # 실패도 기록한다
    assert chk.main(["--root", str(bad_bin_root), "--out", str(out), "30"]) == 1
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["pass"] is False and rep["frames"][0]["mismatches"] == ["frame 30: velodyne missing; cannot check"]


def test_display_path_replaces_home_prefix_only() -> None:
    from kitti import config

    home = Path.home()
    assert config.display_path(home / "data" / "kitti") == "~/data/kitti"
    assert config.display_path(home) == "~"
    assert config.display_path(str(home) + "x/y") == str(home) + "x/y"  # 접두어만 같은 다른 폴더
    assert config.display_path("/tmp/a") == "/tmp/a" and config.display_path(None) is None
    assert Path(config.display_path(home / "a")).expanduser() == home / "a"
