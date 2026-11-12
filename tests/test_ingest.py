"""kitti.ingest 시험: 합성 미니 KITTI 트리(12장면)로 F-1~F-4, D-3 확인."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest
from PIL import Image

from conftest import CALIB_000000
from kitti import config
from kitti import ingest
from kitti import io as kio

N_FRAMES = 12
IMG_W, IMG_H = 100, 50
N_PARTITIONS = 4

GOOD_CAR = "Car 0.00 0 -1.50 10.00 5.00 40.00 30.00 1.50 1.60 3.90 1.00 1.70 20.00 -1.50"
GOOD_PED = "Pedestrian 0.20 1 0.30 50.00 10.00 60.00 45.00 1.70 0.60 0.80 -2.00 1.60 15.00 0.25"
DONTCARE = "DontCare -1 -1 -10 20.00 10.00 30.00 20.00 -1 -1 -1 -1000 -1000 -1000 -10"
# truncated 1.5 → truncated_range, right 150 > 100 px → bbox_outside_image
BAD_RANGE = "Van 1.50 0 0.10 10.00 5.00 150.00 30.00 1.50 1.60 3.90 1.00 1.70 20.00 0.10"
# z < 0 → 3D 무효(일반 객체), 범위 오류는 없음
INVALID_3D = "Cyclist 0.00 0 0.10 10.00 5.00 40.00 30.00 1.50 0.60 1.80 1.00 1.70 -3.00 0.10"

MISSING_IMAGE, MISSING_VELO, MISSING_LABEL, MISSING_CALIB = 3, 5, 7, 8
LABEL_PARSE_ERR = 9
NAN_BIN, BAD_SIZE_BIN, DUP_FRAME = 4, 6, 10


def _labels(fid: int) -> list[str]:
    """프레임별 라벨 줄. 빈 줄은 줄 번호만 소비한다."""
    if fid == 2:
        return [GOOD_CAR, DONTCARE, "", BAD_RANGE, DONTCARE]
    if fid == 1:
        return [GOOD_PED, INVALID_3D]
    if fid == LABEL_PARSE_ERR:
        return [GOOD_CAR, "Car 0.0 0 0.1 1 2 3"]
    if fid == 11:
        return []
    return [GOOD_CAR, GOOD_PED][: 1 + fid % 2]


def _points(fid: int) -> np.ndarray:
    rng = np.random.default_rng(fid)
    n = 40 + 7 * fid
    pts = np.column_stack(
        [rng.uniform(0, 70, n), rng.uniform(-30, 30, n), rng.uniform(-2, 2, n), rng.uniform(0, 1, n)]
    ).astype(np.float32)
    if fid == NAN_BIN:
        pts[0, 0] = np.nan  # non_finite
        pts[1, 3] = np.inf  # non_finite
        pts[2, 1] = 250.0  # coord_range
        pts[3, 2] = -300.0  # coord_range
        pts[4, 3] = 1.5  # reflectance_range
    return pts


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("kitti")
    base = root / "training"
    for sub in config.SUBDIRS:
        (base / sub).mkdir(parents=True)
    expected_objects: dict[int, list[int]] = {}
    expected_points: dict[int, int] = {}
    for fid in range(N_FRAMES):
        name = f"{fid:06d}"
        if fid != MISSING_IMAGE:
            Image.new("RGB", (IMG_W, IMG_H), (fid, 0, 0)).save(base / "image_2" / f"{name}.png")
        if fid != MISSING_CALIB:
            (base / "calib" / f"{name}.txt").write_text(CALIB_000000, encoding="utf-8")
        if fid != MISSING_LABEL:
            lines = _labels(fid)
            (base / "label_2" / f"{name}.txt").write_text(
                "".join(line + "\n" for line in lines), encoding="utf-8"
            )
            if fid != LABEL_PARSE_ERR:
                expected_objects[fid] = [i for i, line in enumerate(lines, 1) if line.strip()]
        if fid == BAD_SIZE_BIN:
            (base / "velodyne" / f"{name}.bin").write_bytes(b"\0" * 50)
        elif fid != MISSING_VELO:
            pts = _points(fid)
            pts.tofile(base / "velodyne" / f"{name}.bin")
            expected_points[fid] = pts.shape[0]
    # 같은 frame_id(10)로 해석되는 두 번째 파일 → 중복. 정본은 6자리 이름 파일.
    np.zeros((3, 4), dtype=np.float32).tofile(base / "velodyne" / f"{DUP_FRAME}.bin")
    (base / "label_2" / "README.txt").write_text("not a frame", encoding="utf-8")
    return {"root": root, "objects": expected_objects, "points": expected_points}


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    config.KITTI_WORK = tmp_path_factory.mktemp("sparkwork")
    session = config.get_spark("local[2]", app="kitti-ingest-test")
    yield session
    session.stop()


@pytest.fixture(scope="module")
def ingested(tree: dict, spark, tmp_path_factory: pytest.TempPathFactory) -> dict:
    work = tmp_path_factory.mktemp("work")
    results = tmp_path_factory.mktemp("results")
    report = ingest.run(
        tree["root"], work, master="local[2]", n_partitions=N_PARTITIONS,
        results_dir=results, spark=spark, allow_incomplete=True,
    )
    return {"report": report, "work": work, "results": results, "pq": work / "parquet"}


def test_index_linking_and_missing(tree: dict) -> None:
    idx = ingest.build_index(tree["root"])
    rep = idx["report"]
    assert rep["n_frames"] == N_FRAMES
    missing = {k: v["ids"] for k, v in rep["missing"].items()}
    assert missing == {
        "image_2": [MISSING_IMAGE],
        "velodyne": [MISSING_VELO],
        "label_2": [MISSING_LABEL],
        "calib": [MISSING_CALIB],
    }
    assert rep["unlinked"]["ids"] == sorted([MISSING_IMAGE, MISSING_VELO, MISSING_LABEL, MISSING_CALIB])
    assert rep["n_linked"] == N_FRAMES - 4
    assert rep["duplicates"]["count"] == 1 and rep["duplicates"]["ids"] == [DUP_FRAME]
    assert len(rep["duplicates"]["files"][str(DUP_FRAME)]["velodyne"]) == 2
    assert idx["frames"][DUP_FRAME]["velodyne"].endswith(f"{DUP_FRAME:06d}.bin")
    assert rep["ignored_files"]["count"] == 1


def test_index_subset_reports_absent_ids(tree: dict) -> None:
    rep = ingest.build_index(tree["root"], [0, 1, 99])["report"]
    assert rep["n_frames"] == 3
    assert all(v["ids"] == [99] for v in rep["missing"].values())


def test_contiguous_chunks() -> None:
    chunks = ingest.contiguous_chunks([5, 1, 3, 2, 4, 9, 7], 3)
    assert chunks == [[1, 2, 3], [4, 5], [7, 9]]
    assert ingest.contiguous_chunks([1, 2], 32) == [[1], [2]]
    assert ingest.contiguous_chunks([], 32) == []


def test_quality_dontcare_separate_and_reasons(ingested: dict) -> None:
    lab = ingested["report"]["labels"]
    assert lab["dontcare"]["count"] == 2
    assert lab["object_errors"]["n_objects_with_error"] == 1
    assert lab["object_errors"]["by_reason"] == {"bbox_outside_image": 1, "truncated_range": 1}
    assert lab["object_errors"]["items"] == [
        {"frame_id": 2, "object_id": 4, "reasons": ["truncated_range", "bbox_outside_image"]}
    ]
    assert lab["invalid_3d"] == {**lab["invalid_3d"], "count": 1, "ids": [[1, 2]]}
    assert lab["parse_errors"]["count"] == 1
    err = lab["parse_errors"]["items"][0]
    assert err["frame_id"] == LABEL_PARSE_ERR and err["path"].endswith(f"{LABEL_PARSE_ERR:06d}.txt")
    assert "expected 15 fields" in err["message"]


def test_quality_points_and_bad_bin(ingested: dict, tree: dict) -> None:
    rep = ingested["report"]
    pts = rep["points"]
    assert pts["by_reason"] == {"non_finite": 2, "coord_range": 2, "reflectance_range": 1}
    assert pts["n_points_with_error"] == 5
    assert pts["frames_with_error"]["ids"] == [NAN_BIN]
    assert pts["bad_bin_size"]["count"] == 1
    assert pts["bad_bin_size"]["items"][0]["frame_id"] == BAD_SIZE_BIN
    assert "not divisible by 16" in pts["bad_bin_size"]["items"][0]["message"]
    d3 = rep["d3_processed"]
    assert d3["n_frames_processed"] == N_FRAMES
    assert d3["total_points"] == sum(tree["points"].values())
    assert d3["bytes"]["velodyne"] == 16 * sum(tree["points"].values())
    base = Path(tree["root"]) / "training"
    assert d3["bytes"]["calib"] == sum(p.stat().st_size for p in (base / "calib").iterdir())
    assert d3["bytes"]["image_2"] == sum(p.stat().st_size for p in (base / "image_2").iterdir())
    label_ok = [p for p in (base / "label_2").glob("0*.txt") if p.stem != f"{LABEL_PARSE_ERR:06d}"]
    assert d3["bytes"]["label_2"] == sum(p.stat().st_size for p in label_ok)
    assert d3["bytes_total"] == sum(d3["bytes"].values())


def test_report_files_written(ingested: dict) -> None:
    js = json.loads((ingested["results"] / "quality_report.json").read_text(encoding="utf-8"))
    assert js["labels"]["dontcare"]["count"] == 2
    assert set(js["timings_s"]) >= {"index", "process_frames", "write_frames", "write_objects", "total"}
    md = (ingested["results"] / "quality_report.md").read_text(encoding="utf-8")
    assert "## 1. 장면 연결 (F-1)" in md and "DontCare (오류와 별도)" in md and "D-3" in md


def test_parquet_counts_and_schemas(ingested: dict, tree: dict, spark) -> None:
    from kitti import schema

    pqd = ingested["pq"]
    for name in ingest.TABLES:
        df = spark.read.parquet(str(pqd / name))
        assert df.columns == schema.ALL_SCHEMAS[name].fieldNames()
        assert [f.dataType for f in df.schema] == [f.dataType for f in schema.ALL_SCHEMAS[name]]
    frames = spark.read.parquet(str(pqd / "frames"))
    assert frames.count() == N_FRAMES
    assert sorted(r[0] for r in frames.select("frame_id").collect()) == list(range(N_FRAMES))
    bad = frames.where(f"frame_id = {BAD_SIZE_BIN}").first()
    assert bad["has_velodyne"] and bad["n_points"] is None and bad["velodyne_bytes"] == 50
    f2 = frames.where("frame_id = 2").first()
    assert (f2["image_w"], f2["image_h"], f2["n_objects"]) == (IMG_W, IMG_H, 4)
    assert len(f2["P2"]) == 12 and len(f2["R0_rect"]) == 9
    assert frames.where(f"frame_id = {MISSING_CALIB}").first()["P2"] is None

    objects = spark.read.parquet(str(pqd / "objects"))
    assert objects.count() == sum(len(v) for v in tree["objects"].values())
    got: dict[int, list[int]] = {}
    for r in objects.orderBy("frame_id", "object_id").select("frame_id", "object_id").collect():
        got.setdefault(r[0], []).append(r[1])
    assert got == {k: v for k, v in tree["objects"].items() if v}
    assert got[2] == [1, 2, 4, 5]  # 빈 줄(3번)은 번호만 소비
    dc = objects.where("class = 'DontCare'").select("is_dontcare", "valid_3d").distinct().collect()
    assert [tuple(r) for r in dc] == [(True, False)]
    assert objects.where("frame_id = 1 AND object_id = 2").first()["valid_3d"] is False

    points = spark.read.parquet(str(pqd / "points"))
    assert points.count() == sum(tree["points"].values())
    per_frame = {r[0]: r[1] for r in points.groupBy("frame_id").count().collect()}
    assert per_frame == tree["points"]
    assert ingested["report"]["parquet"]["points"]["rows"] == sum(tree["points"].values())


def test_points_values_kept_with_original_ids(ingested: dict, tree: dict) -> None:
    files = ingest.points_files_for([NAN_BIN], ingested["work"])
    tbl = pq.read_table(files, filters=[("frame_id", "=", NAN_BIN)])
    src = kio.read_velodyne(Path(tree["root"]) / "training" / "velodyne" / f"{NAN_BIN:06d}.bin")
    assert tbl.column("point_id").to_pylist() == list(range(src.shape[0]))
    got = np.column_stack([tbl.column(c).to_numpy() for c in ("x", "y", "z", "reflectance")])
    np.testing.assert_array_equal(got, src)  # NaN·범위 밖 값도 그대로(NaN 위치 동일)


def test_points_files_row_groups_and_manifest(ingested: dict, tree: dict) -> None:
    pdir = ingested["pq"] / "points"
    manifest = json.loads((pdir / ingest.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["compression"] == "zstd"
    chunks = ingest.contiguous_chunks(list(range(N_FRAMES)), N_PARTITIONS)
    with_points = set(tree["points"])
    expected_files = {}
    for part, c in enumerate(chunks):
        frames = [f for f in c if f in with_points]
        if frames:
            expected_files[ingest.points_file_name(part, c[0], c[-1])] = (c[0], c[-1], frames)
    assert {f["file"] for f in manifest["files"]} == set(expected_files)
    assert sorted(p.name for p in pdir.glob("*.parquet")) == sorted(expected_files)
    for entry in manifest["files"]:
        lo, hi, frames = expected_files[entry["file"]]
        assert (entry["frame_min"], entry["frame_max"], entry["frames"]) == (lo, hi, frames)
        meta = pq.ParquetFile(pdir / entry["file"]).metadata
        assert meta.num_row_groups == len(frames) == entry["n_row_groups"]
        for i, fid in enumerate(frames):
            rg = meta.row_group(i)
            assert rg.num_rows == tree["points"][fid]
            st = rg.column(0).statistics
            assert (st.min, st.max) == (fid, fid)
            assert rg.column(2).compression == "ZSTD"
        assert entry["n_rows"] == sum(tree["points"][f] for f in frames)


def test_points_files_for_selects_files(ingested: dict) -> None:
    manifest = ingest.load_manifest(ingested["work"])
    pdir = ingested["pq"] / "points"
    by_frame = {f: e["file"] for e in manifest["files"] for f in e["frames"]}
    sel = ingest.points_files_for([0, 11], ingested["work"])
    assert sel == sorted({str(pdir / by_frame[0]), str(pdir / by_frame[11])})
    assert ingest.points_files_for([MISSING_VELO, BAD_SIZE_BIN], ingested["work"]) == []
    assert ingest.points_files_for([], ingested["work"]) == []


def test_idempotent_rerun(ingested: dict, tree: dict, spark) -> None:
    pqd = ingested["pq"]

    def listing() -> dict[str, list[str]]:
        # Spark가 쓴 frames/objects 파일명에는 실행마다 다른 UUID가 붙으므로 개수만 비교한다.
        out = {t: sorted(p.name for p in (pqd / t).glob("*.parquet")) for t in ("frames", "objects")}
        return {**{t: [str(len(v))] for t, v in out.items()},
                "points": sorted(p.name for p in (pqd / "points").iterdir())}

    before = listing()
    # 파생 표(L4): 정본 교체 후 낡은 것이므로 지워져야 한다
    for name in ingest.DERIVED_TABLES:
        (pqd / name).mkdir()
        (pqd / name / "part-0.parquet").write_bytes(b"stale")
    rep2 = ingest.run(
        tree["root"], ingested["work"], master="local[2]", n_partitions=N_PARTITIONS,
        results_dir=ingested["results"], spark=spark, allow_incomplete=True,
    )
    assert listing() == before
    assert sorted(p.name for p in pqd.iterdir()) == ["frames", "objects", "points"]
    assert sorted(rep2["parquet"]["removed_derived_tables"]) == sorted(ingest.DERIVED_TABLES)
    assert spark.read.parquet(str(pqd / "objects")).count() == rep2["parquet"]["objects"]["rows"]
    assert spark.read.parquet(str(pqd / "points")).count() == sum(tree["points"].values())
    assert rep2["labels"] == ingested["report"]["labels"]


def test_frames_file_subset(tree: dict, spark, tmp_path: Path) -> None:
    lst = tmp_path / "list.txt"
    lst.write_text("# subset\n2\n4\n\n0\n", encoding="utf-8")
    ids = ingest.read_frames_file(lst)
    assert ids == [2, 4, 0]
    out = tmp_path / "sub" / "parquet"
    rep = ingest.run(
        tree["root"], tmp_path / "work", ids, master="local[2]", n_partitions=N_PARTITIONS,
        results_dir=tmp_path / "res", spark=spark, out_root=out,
    )
    assert rep["d3_processed"]["n_frames_processed"] == 3
    assert rep["parquet"]["points"]["rows"] == sum(tree["points"][f] for f in (0, 2, 4))
    assert not (tmp_path / "work" / "parquet" / "frames").exists()  # 정본 위치는 건드리지 않음
    fs = config.frame_set_info([0, 2, 4])
    assert rep["frame_set"] == fs
    manifest = json.loads((out / "points" / ingest.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["frame_set"] == {**fs, "ids": [0, 2, 4]}
    fman = json.loads((out / "frames" / ingest.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert fman["frame_set"] == manifest["frame_set"] and fman["subset"] is True
    assert fman["rows"]["frames"] == 3
    names = sorted(p.name for p in (out / "points").glob("*.parquet"))
    assert names == [
        "part-00000_f000000-f000000.parquet",
        "part-00001_f000002-f000002.parquet",
        "part-00002_f000004-f000004.parquet",
    ]
    assert os.path.exists(tmp_path / "res" / "quality_report.md")


def test_full_manifest_records_frame_set(ingested: dict) -> None:
    manifest = ingest.load_manifest(ingested["work"])
    assert manifest["frame_set"]["ids"] == list(range(N_FRAMES))
    assert manifest["frame_set"]["sha256"] == config.frame_set_info(range(N_FRAMES))["sha256"]
    fman = json.loads((ingested["pq"] / "frames" / ingest.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert fman["subset"] is False and fman["frame_set"]["n"] == N_FRAMES
    assert fman["completeness"]["complete"] is False  # 일부러 누락 파일을 둔 합성 데이터


def test_subset_refuses_canonical_outputs(tree: dict, tmp_path: Path) -> None:
    """H1: 부분집합이 정본 <work>/parquet·results/를 덮어쓰려 하면 쓰기 전에 거부한다."""
    work = tmp_path / "work"
    with pytest.raises(ingest.IngestRefused, match="canonical"):
        ingest.run(tree["root"], work, [0, 2], results_dir=tmp_path / "res")
    with pytest.raises(ingest.IngestRefused, match="quality_report"):
        ingest.run(tree["root"], work, [0, 2], out_root=tmp_path / "sub")  # results 기본값 = 정본
    assert not work.exists() and not (tmp_path / "sub").exists() and not (tmp_path / "res").exists()


def test_incomplete_input_refused(tree: dict, tmp_path: Path) -> None:
    """M1: 대상 장면 연결이 불완전하면 --allow-incomplete 없이 거부하고 아무것도 바꾸지 않는다."""
    work = tmp_path / "work"
    with pytest.raises(ingest.IngestRefused, match="incomplete"):
        ingest.run(tree["root"], work, results_dir=tmp_path / "res")
    with pytest.raises(ingest.IngestRefused, match="linked 1 of 2"):
        ingest.run(tree["root"], work, [0, 99], results_dir=tmp_path / "res", out_root=tmp_path / "sub")
    with pytest.raises(ingest.IngestRefused, match="velodyne"):
        ingest.run(tree["root"], work, [0, MISSING_VELO], results_dir=tmp_path / "res",
                   out_root=tmp_path / "sub")
    assert not work.exists() and not (tmp_path / "sub").exists() and not (tmp_path / "res").exists()


def test_completeness_rules(tree: dict) -> None:
    idx = ingest.build_index(tree["root"])
    full = ingest.completeness(idx["report"], idx["frames"], None)
    assert full["n_expected"] == N_FRAMES - 1  # label_2가 있는 ID
    assert not full["complete"] and full["missing_by_kind"]["velodyne"] == 1
    sub = ingest.build_index(tree["root"], [0, 1, 2])
    assert ingest.completeness(sub["report"], sub["frames"], [0, 1, 2])["complete"]


def test_cli_refusal_exit_code(tree: dict, tmp_path: Path) -> None:
    lst = tmp_path / "list.txt"
    lst.write_text("0\n2\n", encoding="utf-8")
    code = ingest.main(["--root", str(tree["root"]), "--work", str(tmp_path / "w"),
                        "--frames-file", str(lst), "--results", str(tmp_path / "r")])
    assert code == 2 and not (tmp_path / "w").exists()
