"""kitti.export 시험: 합성 Parquet → CSV 형식·결정적 순서·기대값(행 수·NULL 수·기준값)."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kitti import export as ke
from kitti import features as kf

DB_DIR = Path(__file__).resolve().parents[1] / "db"


def _obj(frame_id, object_id, cls, points, *, z=10.0, x=1.0, h=1.5, occluded=0, truncated=0.0,
         bbox_h=50.0, diff=1):
    dc = cls == "DontCare"
    valid = (not dc) and h > 0 and z > 0
    return {
        "frame_id": frame_id, "object_id": object_id, "class": cls,
        "truncated": -1.0 if dc else truncated, "occluded": -1 if dc else occluded,
        "alpha": -10.0 if dc else 0.25,
        "bbox_left": 100.0, "bbox_top": 100.0, "bbox_right": 150.5, "bbox_bottom": 100.0 + bbox_h,
        "h": -1.0 if dc else h, "w": -1.0 if dc else 0.6, "l": -1.0 if dc else 0.8,
        "x": -1000.0 if dc else x, "y": -1000.0 if dc else 1.6, "z": -1000.0 if dc else z,
        "ry": -10.0 if dc else 0.1,
        "is_dontcare": dc, "valid_3d": valid,
        "dist_m": None if dc else math.sqrt(x * x + z * z),
        "bbox_height": bbox_h,
        "eligible_easy": (not dc) and diff == 1,
        "eligible_moderate": (not dc) and diff <= 2,
        "eligible_hard": (not dc) and diff <= 3,
        "diff_level": 4 if dc else diff,
        "points_in_box": None if (dc or not valid) else points,
        "dist_bin": None if dc else "0-10" if math.sqrt(x * x + z * z) < 10 else "10-20",
    }


def _objects():
    objs = [_obj(1, 1, "Car", p, z=5.0 + p) for p in range(1, 13)]  # Car 12개: ceil(12/10)=2 → 정렬 2번째
    objs += [_obj(1, 13, "DontCare", None), _obj(0, 2, "DontCare", None)]
    objs += [_obj(0, 1, "Pedestrian", 7, occluded=3, diff=4)]
    objs += [_obj(2, 1, "Car", None, z=-1.0)]  # 무효 3D → points NULL
    # object_id를 겹치지 않게 다시 매긴다(Car 12개가 같은 장면).
    for i, o in enumerate(objs[:12], start=1):
        o["object_id"] = i
    return objs


@pytest.fixture
def work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    feats = work / "parquet" / "object_features"
    frames = work / "parquet" / "frames"
    feats.mkdir(parents=True)
    frames.mkdir(parents=True)
    objs = _objects()
    # 일부러 순서를 섞고 파일 두 개로 나눈다 → 출력은 (frame_id, object_id) 오름차순이어야 한다.
    rev = list(reversed(objs))
    pq.write_table(pa.Table.from_pylist(rev[:7], schema=kf.ARROW_SCHEMA), feats / "part-1.parquet")
    pq.write_table(pa.Table.from_pylist(rev[7:], schema=kf.ARROW_SCHEMA), feats / "part-0.parquet")
    frame_rows = [
        {"frame_id": 2, "has_image": True, "has_velodyne": True, "has_label": True, "has_calib": True,
         "image_w": 1242, "image_h": 375, "n_points": 100},
        {"frame_id": 0, "has_image": True, "has_velodyne": False, "has_label": True, "has_calib": True,
         "image_w": 1242, "image_h": 375, "n_points": None},
        {"frame_id": 1, "has_image": True, "has_velodyne": True, "has_label": True, "has_calib": True,
         "image_w": 1224, "image_h": 370, "n_points": 120},
    ]
    pq.write_table(pa.Table.from_pylist(frame_rows), frames / "part-0.parquet")
    return work


def _read_csv(path: Path) -> list[list[str]]:
    return [line.split(",") for line in path.read_text(encoding="utf-8").splitlines()]


def test_export_csv_format_and_order(work: Path, tmp_path: Path) -> None:
    out = tmp_path / "csv"
    ke.export(work, out)

    frame = _read_csv(out / "frame.csv")
    assert frame[0] == list(ke.FRAME_COLUMNS)
    assert [r[0] for r in frame[1:]] == ["0", "1", "2"]
    assert frame[1] == ["0", "1242", "375", "\\N", "missing_file"]
    assert frame[2][4] == "ok"

    gt = _read_csv(out / "gt_object.csv")
    assert gt[0] == [c for c, _ in ke.GT_OBJECT_COLUMNS]
    keys = [(int(r[0]), int(r[1])) for r in gt[1:]]
    assert keys == sorted(keys) and len(keys) == 16
    dc = next(r for r in gt[1:] if r[2] == "DontCare")
    # 원본 표식은 그대로 내보낸다(NULL 변환은 02_load.sql이 한다). dist_m·dist_bin은 \N.
    assert dc[3] == "-1.0" and dc[13] == "-1000.0"
    assert dc[gt[0].index("dist_m")] == "\\N" and dc[gt[0].index("dist_bin")] == "\\N"
    assert dc[gt[0].index("valid_3d")] == "0"
    # 부동소수는 왕복 가능해야 한다.
    car = gt[3]
    original = next(o for o in _objects() if (o["frame_id"], o["object_id"]) == (int(car[0]), int(car[1])))
    assert float(car[gt[0].index("dist_m")]) == original["dist_m"]

    feat = _read_csv(out / "object_feature.csv")
    assert feat[0] == list(ke.OBJECT_FEATURE_COLUMNS)
    assert len(feat) - 1 == 14  # DontCare 2개 제외
    assert ["2", "1", "\\N"] in feat  # 무효 3D
    assert all(r[2] != "" for r in feat[1:])


def test_export_is_deterministic(work: Path, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    ke.export(work, a)
    ke.export(work, b)
    for name in ("frame.csv", "gt_object.csv", "object_feature.csv"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_expected_values(work: Path, tmp_path: Path) -> None:
    exp = ke.export(work, tmp_path / "csv")
    saved = json.loads((tmp_path / "csv" / "expected.json").read_text(encoding="utf-8"))
    assert saved["row_count"] == exp["row_count"]
    assert exp["row_count"] == {
        "frame": 3, "object_class": 9, "gt_object": 16, "object_feature": 14, "analysis_threshold": 2,
        "stg_frame": 3, "stg_gt_object": 16, "stg_object_feature": 14,
    }
    nc = exp["null_count"]
    assert nc["gt_object.truncated"] == 2 and nc["gt_object.loc_z"] == 2
    assert nc["gt_object.bbox_left"] == 0 and nc["gt_object.bbox_h"] == 0
    assert nc["gt_object.dist_m"] == 2
    assert nc["gt_object.diff_level"] == 3  # DontCare 2 + 해당 없음 1
    assert nc["gt_object.valid_3d"] == 2
    assert nc["object_feature.points_in_box"] == 1
    assert nc["frame.n_points"] == 1
    assert exp["threshold"] == {"Car": 2, "Pedestrian": 7}
    assert exp["threshold_n"] == {"Car": 12, "Pedestrian": 1}
    assert set(exp["contradiction"]) == set(ke.CONTRADICTION_ITEMS)
    assert all(v == 0 for v in exp["contradiction"].values())
    assert exp["meta"]["threshold_source"] == "parquet"


def test_spark_thresholds_must_match(work: Path, tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text("class,n,k,threshold\nCar,12,2,2\nPedestrian,1,1,7\n", encoding="utf-8")
    exp = ke.export(work, tmp_path / "ok", spark_thresholds=good)
    assert exp["meta"]["threshold_source"] == str(good)

    bad = tmp_path / "bad.csv"
    bad.write_text("class,n,k,threshold\nCar,12,2,3\nPedestrian,1,1,7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differ"):
        ke.export(work, tmp_path / "bad", spark_thresholds=bad)


def _ntile10_bucket1_max(values: list[int]) -> int:
    """MySQL NTILE(10) 의미대로 나눈 1번 구간의 최댓값."""
    n = len(values)
    s = sorted(values)
    base, extra = divmod(n, 10)
    first = base + (1 if extra > 0 else 0)
    return s[first - 1]


@pytest.mark.parametrize("n", [1, 2, 9, 10, 11, 19, 20, 21, 99, 101, 2875 * 10 - 9])
def test_threshold_equals_ntile_bucket1_max(n: int) -> None:
    values = [(i * 7919) % 97 for i in range(n)]
    objs = [{"class": "Car", "valid_3d": True, "is_dontcare": False, "points_in_box": v} for v in values]
    got = ke.compute_thresholds(objs)["Car"]
    assert got["k"] == math.ceil(n / 10)
    assert got["threshold"] == _ntile10_bucket1_max(values)


def test_format_value() -> None:
    assert ke.format_value(None) == "\\N"
    assert ke.format_value(True) == "1" and ke.format_value(False) == "0"
    assert ke.format_value(0.1) == "0.1"
    assert ke.format_value(7) == "7"
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ke.format_value(bad)
    with pytest.raises(ValueError):
        ke.format_value("a,b")


def test_frame_quality(tmp_path: Path) -> None:
    row = {"frame_id": 5, "has_image": True, "has_velodyne": True, "has_label": True,
           "has_calib": True, "n_points": 10}
    assert ke.frame_quality(row, set()) == "ok"
    assert ke.frame_quality(row, {5}) == "bad_value"
    assert ke.frame_quality({**row, "has_calib": False}, set()) == "missing_file"
    report = tmp_path / "q.json"
    report.write_text(json.dumps({"points": {"frames_with_error": {"ids": [3, 5]}}}), encoding="utf-8")
    assert ke.bad_value_frames(report) == {3, 5}
    assert ke.bad_value_frames(tmp_path / "none.json") == set()


def test_expected_keys_match_checks_sql(work: Path, tmp_path: Path) -> None:
    """expected.json의 모든 (section, item)을 04_checks.sql이 실제로 내보낸다."""
    exp = ke.export(work, tmp_path / "csv")
    sql = (DB_DIR / "04_checks.sql").read_text(encoding="utf-8")
    emitted = set(re.findall(r"'(row_count|null_count|contradiction)'(?:\s+AS\s+section)?,\s*'([a-z_0-9.]+)'", sql))
    for section in ("row_count", "null_count", "contradiction"):
        for item in exp[section]:
            assert (section, item) in emitted, f"04_checks.sql lacks {section}/{item}"
    assert {s for s, _ in emitted} == {"row_count", "null_count", "contradiction"}
    assert len({i for s, i in emitted if s == "contradiction"}) == len(ke.CONTRADICTION_ITEMS)


def test_cli(work: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli"
    rc = ke.main(["--work", str(work), "--out", str(out), "--thresholds", str(tmp_path / "none.csv"),
                  "--quality-report", str(tmp_path / "none.json")])
    assert rc == 0
    assert (out / "expected.json").exists()
    assert "gt_object.csv=16" in capsys.readouterr().out
