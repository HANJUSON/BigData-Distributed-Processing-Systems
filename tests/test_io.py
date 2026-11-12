"""kitti.io 파서 시험."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from kitti import io as kio


def test_label_line_numbering_keeps_dontcare(label_file: Path) -> None:
    objs = kio.parse_label(label_file)
    assert [o["object_id"] for o in objs] == [1, 2, 3, 4, 5]
    assert [o["class"] for o in objs] == ["Truck", "Car", "Cyclist", "DontCare", "DontCare"]
    assert [o["is_dontcare"] for o in objs] == [False, False, False, True, True]
    car = objs[1]
    assert car["occluded"] == 0 and isinstance(car["occluded"], int)
    assert car["bbox_top"] == pytest.approx(181.54)
    assert (car["h"], car["w"], car["l"]) == (1.67, 1.87, 3.69)
    assert (car["x"], car["y"], car["z"], car["ry"]) == (-16.53, 2.39, 58.49, 1.57)
    dc = objs[3]
    assert (dc["truncated"], dc["occluded"], dc["alpha"]) == (-1.0, -1, -10.0)
    assert (dc["h"], dc["x"], dc["z"], dc["ry"]) == (-1.0, -1000.0, -1000.0, -10.0)
    # 15개 원본 필드 + object_id + is_dontcare
    assert set(kio.LABEL_FIELDS) | {"object_id", "is_dontcare"} == set(car)


def test_label_blank_line_consumes_line_number(tmp_path: Path) -> None:
    lines = Path(tmp_path / "x.txt")
    lines.write_text(
        "DontCare -1 -1 -10 1 2 3 4 -1 -1 -1 -1000 -1000 -1000 -10\n"
        "\n"
        "Car 0.5 2 0.1 1 2 3 40 1.5 1.6 3.9 1.0 1.7 20.0 0.2\n",
        encoding="utf-8",
    )
    objs = kio.parse_label(lines)
    assert [o["object_id"] for o in objs] == [1, 3]


def test_label_malformed_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text("Car 0 0 0 1 2 3 4\n", encoding="utf-8")
    with pytest.raises(kio.KittiParseError, match="line 1"):
        kio.parse_label(bad)
    bad.write_text("Car 0 0 0 1 2 3 4 1 1 1 1 1 abc 0\n", encoding="utf-8")
    with pytest.raises(kio.KittiParseError):
        kio.parse_label(bad)


@pytest.mark.parametrize("occ", ["2.7", "0.5", "nan", "inf"])
def test_label_non_integral_occluded_rejected(tmp_path: Path, occ: str) -> None:
    bad = tmp_path / "occ.txt"
    bad.write_text(f"Car 0.0 {occ} 0.1 1 2 3 40 1.5 1.6 3.9 1.0 1.7 20.0 0.2\n", encoding="utf-8")
    with pytest.raises(kio.KittiParseError, match="occluded must be an integer"):
        kio.parse_label(bad)


def test_label_integral_float_occluded_accepted(tmp_path: Path) -> None:
    ok = tmp_path / "occ.txt"
    ok.write_text("Car 0.0 2.0 0.1 1 2 3 40 1.5 1.6 3.9 1.0 1.7 20.0 0.2\n", encoding="utf-8")
    obj = kio.parse_label(ok)[0]
    assert obj["occluded"] == 2 and isinstance(obj["occluded"], int)


def test_parse_calib_real_values(calib_file: Path) -> None:
    calib = kio.parse_calib(calib_file)
    assert set(calib) == set(kio.CALIB_KEYS)
    for key, shape in kio.CALIB_KEYS.items():
        assert calib[key].shape == shape
        assert calib[key].dtype == np.float64
    assert calib["P2"][0, 0] == 707.0493
    assert calib["P2"][0, 3] == 45.75831
    assert calib["R0_rect"][2, 2] == 0.9999556
    assert calib["Tr_velo_to_cam"][2, 3] == -0.3321029
    assert calib["Tr_imu_to_velo"][0, 3] == -0.8086759


def test_parse_calib_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "c.txt"
    path.write_text("P0: 1 2 3 4 5 6 7 8 9 10 11 12\n", encoding="utf-8")
    with pytest.raises(kio.KittiParseError, match="missing"):
        kio.parse_calib(path)


def test_read_velodyne(velo_file: Path) -> None:
    pts = kio.read_velodyne(velo_file)
    assert pts.shape == (3, 4) and pts.dtype == np.float32
    assert pts[1, 1] == np.float32(-3.25)
    assert kio.velodyne_point_count(velo_file) == 3


def test_read_velodyne_bad_size(tmp_path: Path) -> None:
    path = tmp_path / "bad.bin"
    path.write_bytes(b"\x00" * 20)
    with pytest.raises(kio.KittiParseError, match="divisible"):
        kio.read_velodyne(path)
    with pytest.raises(kio.KittiParseError):
        kio.velodyne_point_count(path)


def test_image_size_png_header(tmp_path: Path) -> None:
    path = tmp_path / "000000.png"
    Image.new("RGB", (37, 11), color=(1, 2, 3)).save(path)
    assert kio.image_size(path) == (37, 11)
    not_png = tmp_path / "x.png"
    not_png.write_bytes(b"GIF89a" + b"\x00" * 30)
    with pytest.raises(kio.KittiParseError):
        kio.image_size(not_png)


def test_list_frame_ids(tmp_path: Path) -> None:
    base = tmp_path / "training"
    for sub in ("label_2", "calib"):
        (base / sub).mkdir(parents=True)
    for fid in (2, 0, 1):
        (base / "label_2" / f"{fid:06d}.txt").write_text("", encoding="utf-8")
    (base / "calib" / "000001.txt").write_text("", encoding="utf-8")
    (base / "calib" / "readme.txt").write_text("", encoding="utf-8")
    (base / "calib" / "000003.bin").write_bytes(b"")
    ids = kio.list_frame_ids(tmp_path)
    assert ids == {"image_2": [], "velodyne": [], "label_2": [0, 1, 2], "calib": [1]}
    assert kio.frame_path(tmp_path, "velodyne", 7) == base / "velodyne" / "000007.bin"
