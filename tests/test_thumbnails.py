"""kitti.thumbnails 시험: 합성 PNG·라벨로 크기·형식·박스·멱등·누락 보고를 확인한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from kitti import thumbnails as kt
from kitti.analysis import CLASS_COLORS

LABEL = (
    "Car 0.00 0 1.85 100.00 100.00 600.00 300.00 1.67 1.87 3.69 -16.53 2.39 58.49 1.57\n"
    "DontCare -1 -1 -10 700.00 50.00 1200.00 350.00 -1 -1 -1 -1000 -1000 -1000 -10\n"
)


def _hex(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "kitti"
    img_dir = root / "training" / "image_2"
    lbl_dir = root / "training" / "label_2"
    img_dir.mkdir(parents=True)
    lbl_dir.mkdir(parents=True)
    Image.new("RGB", (1242, 375), (128, 128, 128)).save(img_dir / "000000.png")
    Image.new("RGB", (1224, 370), (40, 40, 40)).save(img_dir / "000001.png")
    (lbl_dir / "000000.txt").write_text(LABEL, encoding="utf-8")
    return root


def test_render_size_and_boxes(root: Path) -> None:
    img = kt.render(root / "training" / "image_2" / "000000.png", root / "training" / "label_2" / "000000.txt")
    assert img.size == (320, round(375 * 320 / 1242))
    scale = 320 / 1242
    car = _hex(CLASS_COLORS["Car"])
    # Car 박스 왼쪽 변 위의 픽셀은 클래스 색, DontCare 영역 안쪽은 배경색 그대로.
    left_edge = img.getpixel((round(100 * scale), round(200 * scale)))
    assert left_edge == car
    inside_dc = img.getpixel((round(700 * scale) + 1, round(50 * scale) + 1))
    assert inside_dc == (128, 128, 128)
    assert img.getpixel((round(1000 * scale), round(200 * scale))) == (128, 128, 128)


def test_generate_writes_all_and_is_idempotent(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "thumbs"
    first = kt.generate([0, 1], root, out, workers=2)
    assert first["n_created"] == 2 and first["n_present"] == 2 and first["missing"] == []
    assert first["n_over_max_bytes"] == 0 and 0 < first["bytes_max"] <= kt.MAX_BYTES
    p0 = kt.thumb_path(out, 0)
    assert p0.name == f"000000{kt.EXT}"
    with Image.open(p0) as im:
        assert im.format == kt.FORMAT and im.width == 320
    with Image.open(kt.thumb_path(out, 1)) as im:  # 라벨 없는 장면도 만든다
        assert im.size == (320, round(370 * 320 / 1224))
    assert not list(out.glob(".*.tmp"))

    second = kt.generate([0, 1], root, out, workers=1)
    assert second["n_skipped"] == 2 and second["n_created"] == 0

    # 원본이 더 새로우면 다시 만든다.
    label = root / "training" / "label_2" / "000000.txt"
    newer = p0.stat().st_mtime + 10
    os.utime(label, (newer, newer))
    third = kt.generate([0, 1], root, out, workers=1)
    assert third["n_created"] == 1 and third["n_skipped"] == 1

    forced = kt.generate([0, 1], root, out, workers=1, force=True)
    assert forced["n_created"] == 2


def test_missing_image_is_reported(root: Path, tmp_path: Path) -> None:
    summary = kt.generate([0, 7], root, tmp_path / "t", workers=1)
    assert summary["n_missing_image"] == 1
    assert summary["missing"] == [7]


def test_save_lowers_quality_when_too_big(tmp_path: Path) -> None:
    noise = Image.effect_noise((320, 97), 100).convert("RGB")
    size, quality = kt.save(noise, tmp_path / f"n{kt.EXT}", max_bytes=1500)
    assert quality == kt.QUALITY_STEPS[-1] or size <= 1500
    big_size, big_q = kt.save(noise, tmp_path / f"b{kt.EXT}", max_bytes=10**9)
    assert big_q == kt.QUALITY_STEPS[0]
    assert size <= big_size


def test_cli(root: Path, tmp_path: Path) -> None:
    out, results = tmp_path / "o", tmp_path / "r"
    rc = kt.main(["--root", str(root), "--out", str(out), "--workers", "1", "--results", str(results)])
    assert rc == 0
    summary = json.loads((results / "u3_thumbnails.json").read_text(encoding="utf-8"))
    assert summary["n_frames"] == 2 and summary["n_present"] == 2
