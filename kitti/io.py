"""KITTI 원본 파일 파서 (순수 함수, Spark 의존 없음).

- label_2/<id>.txt : 객체 한 줄당 15개 필드. object_id = 1부터 시작하는 원본 줄 번호
- calib/<id>.txt   : P0..P3(3x4), R0_rect(3x3), Tr_velo_to_cam(3x4), Tr_imu_to_velo(3x4)
- velodyne/<id>.bin: float32 (x, y, z, reflectance) 레코드 16바이트. point_id = 0부터 시작하는 레코드 순서
- image_2/<id>.png : PNG 헤더(IHDR)만 읽어 크기를 얻는다
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any

import numpy as np

from . import config

# label_2 한 줄의 15개 필드 이름(순서 = 파일 열 순서)
LABEL_FIELDS: tuple[str, ...] = (
    "class",  # 객체 종류 문자열
    "truncated",  # 0~1, 이미지 경계 밖으로 잘린 비율 (DontCare: -1)
    "occluded",  # 0~3 정수 가림 정도 (DontCare: -1)
    "alpha",  # rad, 관측 각도 [-π, π] (DontCare: -10)
    "bbox_left",  # px, 2D 박스
    "bbox_top",  # px
    "bbox_right",  # px
    "bbox_bottom",  # px
    "h",  # m, 3D 높이 (DontCare: -1)
    "w",  # m, 3D 너비
    "l",  # m, 3D 길이
    "x",  # m, 카메라(rect) 좌표 박스 바닥 중심 (DontCare: -1000)
    "y",  # m
    "z",  # m
    "ry",  # rad, 카메라 y축 회전 rotation_y [-π, π] (DontCare: -10)
)

CALIB_KEYS: dict[str, tuple[int, int]] = {
    "P0": (3, 4),
    "P1": (3, 4),
    "P2": (3, 4),
    "P3": (3, 4),
    "R0_rect": (3, 3),
    "Tr_velo_to_cam": (3, 4),
    "Tr_imu_to_velo": (3, 4),
}

VELO_RECORD_BYTES: int = 16  # float32 × 4
_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
_FRAME_STEM = re.compile(r"^\d{6}$")


class KittiParseError(ValueError):
    """원본 파일 형식 오류. 품질 검사(F-2)에서 건수로 집계한다."""


def frame_path(root: Path | str, subdir: str, frame_id: int) -> Path:
    """`<root>/training/<subdir>/<frame_id:06d><ext>` 경로."""
    return config.training_dir(root) / subdir / f"{frame_id:06d}{config.SUBDIRS[subdir]}"


def list_frame_ids(root: Path | str | None = None) -> dict[str, list[int]]:
    """하위 폴더별로 존재하는 frame_id(정수) 목록을 오름차순으로 돌려준다.

    파일명이 6자리 숫자 + 해당 확장자인 파일만 센다. 폴더가 없으면 빈 목록.
    """
    base = config.training_dir(root)
    result: dict[str, list[int]] = {}
    for subdir, ext in config.SUBDIRS.items():
        folder = base / subdir
        ids: list[int] = []
        if folder.is_dir():
            for entry in folder.iterdir():
                if entry.suffix == ext and _FRAME_STEM.match(entry.stem):
                    ids.append(int(entry.stem))
        result[subdir] = sorted(ids)
    return result


def parse_label_line(line: str, object_id: int) -> dict[str, Any]:
    """label_2 한 줄을 dict로 변환한다. 숫자 필드는 float(occluded는 int)."""
    parts = line.split()
    if len(parts) != len(LABEL_FIELDS):
        raise KittiParseError(
            f"line {object_id}: expected {len(LABEL_FIELDS)} fields, got {len(parts)}"
        )
    rec: dict[str, Any] = {"object_id": object_id, "class": parts[0]}
    try:
        for name, raw in zip(LABEL_FIELDS[1:], parts[1:]):
            rec[name] = float(raw)
    except ValueError as exc:
        raise KittiParseError(f"line {object_id}: non-numeric field ({exc})") from exc
    occ = rec["occluded"]
    if not occ.is_integer():  # 2.7 같은 값을 잘라 2로 만들지 않는다(NaN·Inf도 여기서 거부)
        raise KittiParseError(f"line {object_id}: occluded must be an integer, got {parts[2]!r}")
    rec["occluded"] = int(occ)
    rec["is_dontcare"] = parts[0] == config.DONTCARE
    return rec


def parse_label(path: Path | str) -> list[dict[str, Any]]:
    """label_2 파일 전체를 객체 dict 목록으로 읽는다.

    object_id는 1부터 시작하는 **원본 줄 번호**이며 어떤 필터링보다 먼저 붙인다.
    빈 줄은 객체가 아니므로 건너뛰지만 줄 번호는 소비한다(이후 번호가 밀리지 않음).
    각 dict는 LABEL_FIELDS 15개 + object_id + is_dontcare 키를 가진다.

    Raises:
        KittiParseError: 필드 수가 15가 아니거나 숫자가 아닌 값이 있을 때.
    """
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                records.append(parse_label_line(line, line_no))
            except KittiParseError as exc:
                raise KittiParseError(f"{path}: {exc}") from exc
    return records


def parse_calib(path: Path | str) -> dict[str, np.ndarray]:
    """calib 파일을 행렬 dict로 읽는다(np.float64).

    Returns:
        P0..P3 (3,4), R0_rect (3,3), Tr_velo_to_cam (3,4), Tr_imu_to_velo (3,4).

    Raises:
        KittiParseError: 필요한 키가 없거나 값 개수가 맞지 않을 때.
    """
    raw: dict[str, list[float]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if ":" not in line:
                continue
            key, values = line.split(":", 1)
            try:
                raw[key.strip()] = [float(v) for v in values.split()]
            except ValueError as exc:
                raise KittiParseError(f"{path}: {key.strip()}: non-numeric value") from exc
    calib: dict[str, np.ndarray] = {}
    for key, shape in CALIB_KEYS.items():
        if key not in raw:
            raise KittiParseError(f"{path}: missing calib key {key}")
        vals = raw[key]
        if len(vals) != shape[0] * shape[1]:
            raise KittiParseError(
                f"{path}: {key} expects {shape[0] * shape[1]} values, got {len(vals)}"
            )
        calib[key] = np.asarray(vals, dtype=np.float64).reshape(shape)
    return calib


def read_velodyne(path: Path | str) -> np.ndarray:
    """velodyne .bin을 (N, 4) float32 배열(x, y, z, reflectance)로 읽는다.

    행 번호가 곧 point_id(0부터)이므로 어떤 행도 제거하지 않는다.
    좌표 단위는 m(Velodyne 좌표: x 전방, y 좌측, z 위), reflectance는 0~1.

    Raises:
        KittiParseError: 파일 크기가 16바이트 배수가 아닐 때.
    """
    size = Path(path).stat().st_size
    if size % VELO_RECORD_BYTES != 0:
        raise KittiParseError(f"{path}: size {size} not divisible by {VELO_RECORD_BYTES}")
    return np.fromfile(path, dtype=np.float32).reshape(-1, 4)


def velodyne_point_count(path: Path | str) -> int:
    """파일 크기만으로 포인트 수를 계산한다(전체 읽기 없음)."""
    size = Path(path).stat().st_size
    if size % VELO_RECORD_BYTES != 0:
        raise KittiParseError(f"{path}: size {size} not divisible by {VELO_RECORD_BYTES}")
    return size // VELO_RECORD_BYTES


def image_size(path: Path | str) -> tuple[int, int]:
    """PNG 헤더(IHDR)만 읽어 (width, height) px를 돌려준다. 이미지는 디코딩하지 않는다.

    Raises:
        KittiParseError: PNG 서명이나 IHDR 청크가 올바르지 않을 때.
    """
    with open(path, "rb") as fh:
        head = fh.read(24)
    if len(head) < 24 or head[:8] != _PNG_SIGNATURE or head[12:16] != b"IHDR":
        raise KittiParseError(f"{path}: not a valid PNG header")
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)
