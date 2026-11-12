"""Spark 테이블 명시적 스키마 (README 7.3, F-3).

각 필드의 단위·좌표계·널 의미는 StructField metadata의 "comment"/"unit"에 기록한다.
- frames         : 장면 하나 / (frame_id)
- objects        : 정답 객체 하나 / (frame_id, object_id) — DontCare 포함 전 행 보존
- points         : LiDAR 포인트 하나 / (frame_id, point_id)
- object_features: objects 확장 / (frame_id, object_id)
- grid_stats     : 장면별 BEV 격자 하나 / (frame_id, grid_x, grid_y)
"""

from __future__ import annotations

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DataType,
    DoubleType,
    FloatType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)


def _f(name: str, dtype: DataType, nullable: bool, comment: str, unit: str = "") -> StructField:
    """metadata(comment, unit)를 붙인 StructField."""
    meta = {"comment": comment}
    if unit:
        meta["unit"] = unit
    return StructField(name, dtype, nullable, metadata=meta)


def _matrix(name: str, shape: str) -> StructField:
    # Parquet 왕복 시 배열 원소는 nullable로 읽히므로 containsNull=True로 둔다.
    return _f(name, ArrayType(DoubleType(), True), True, f"calib {name} {shape}, row-major")


FRAMES = StructType(
    [
        _f("frame_id", IntegerType(), False, "파일명 6자리 숫자를 정수로 변환한 장면 ID"),
        _f("image_path", StringType(), True, "image_2 PNG 절대 경로, 없으면 NULL"),
        _f("velodyne_path", StringType(), True, "velodyne .bin 절대 경로, 없으면 NULL"),
        _f("label_path", StringType(), True, "label_2 .txt 절대 경로, 없으면 NULL"),
        _f("calib_path", StringType(), True, "calib .txt 절대 경로, 없으면 NULL"),
        _f("has_image", BooleanType(), False, "image_2 파일 존재 여부"),
        _f("has_velodyne", BooleanType(), False, "velodyne 파일 존재 여부"),
        _f("has_label", BooleanType(), False, "label_2 파일 존재 여부"),
        _f("has_calib", BooleanType(), False, "calib 파일 존재 여부"),
        _f("image_w", IntegerType(), True, "PNG 헤더의 이미지 너비", "px"),
        _f("image_h", IntegerType(), True, "PNG 헤더의 이미지 높이", "px"),
        _f("n_points", LongType(), True, ".bin 레코드 수 = 파일 크기 / 16", "count"),
        _f("velodyne_bytes", LongType(), True, ".bin 파일 크기", "byte"),
        _f("n_objects", IntegerType(), True, "label_2 객체 줄 수(DontCare 포함)", "count"),
        _matrix("P0", "3x4"),
        _matrix("P1", "3x4"),
        _matrix("P2", "3x4"),
        _matrix("P3", "3x4"),
        _matrix("R0_rect", "3x3"),
        _matrix("Tr_velo_to_cam", "3x4"),
        _matrix("Tr_imu_to_velo", "3x4"),
    ]
)

_OBJECT_FIELDS: list[StructField] = [
    _f("frame_id", IntegerType(), False, "장면 ID"),
    _f("object_id", IntegerType(), False, "label_2 파일의 1부터 시작하는 원본 줄 번호"),
    _f("class", StringType(), False, "객체 종류(Car, Pedestrian, ..., DontCare)"),
    _f("truncated", DoubleType(), False, "잘림 비율 0~1 (DontCare: -1)", "ratio"),
    _f("occluded", IntegerType(), False, "가림 정도 0~3 (DontCare: -1)", "level"),
    _f("alpha", DoubleType(), False, "관측 각도 (DontCare: -10)", "rad"),
    _f("bbox_left", DoubleType(), False, "2D 박스 왼쪽", "px"),
    _f("bbox_top", DoubleType(), False, "2D 박스 위쪽", "px"),
    _f("bbox_right", DoubleType(), False, "2D 박스 오른쪽", "px"),
    _f("bbox_bottom", DoubleType(), False, "2D 박스 아래쪽", "px"),
    _f("h", DoubleType(), False, "3D 높이 (DontCare: -1)", "m"),
    _f("w", DoubleType(), False, "3D 너비 (DontCare: -1)", "m"),
    _f("l", DoubleType(), False, "3D 길이 (DontCare: -1)", "m"),
    _f("x", DoubleType(), False, "박스 바닥 중심 x, 카메라 rect 좌표 (DontCare: -1000)", "m"),
    _f("y", DoubleType(), False, "박스 바닥 중심 y, 카메라 rect 좌표, 아래가 + (DontCare: -1000)", "m"),
    _f("z", DoubleType(), False, "박스 바닥 중심 z, 카메라 rect 좌표, 전방 (DontCare: -1000)", "m"),
    _f("ry", DoubleType(), False, "카메라 y축 회전 rotation_y (DontCare: -10)", "rad"),
    _f("is_dontcare", BooleanType(), False, "class == 'DontCare'"),
    _f("valid_3d", BooleanType(), False, "not DontCare and h,w,l > 0 and z > 0"),
]

OBJECTS = StructType(_OBJECT_FIELDS)

POINTS = StructType(
    [
        _f("frame_id", IntegerType(), False, "장면 ID"),
        _f("point_id", IntegerType(), False, ".bin 안 0부터 시작하는 레코드 순서"),
        _f("x", FloatType(), False, "Velodyne 좌표 x(전방), 원본 float32", "m"),
        _f("y", FloatType(), False, "Velodyne 좌표 y(좌측), 원본 float32", "m"),
        _f("z", FloatType(), False, "Velodyne 좌표 z(위), 원본 float32", "m"),
        _f("reflectance", FloatType(), False, "반사 강도, 원본 float32", "0-1"),
    ]
)

OBJECT_FEATURES = StructType(
    _OBJECT_FIELDS
    + [
        _f("dist_m", DoubleType(), True, "카메라 좌표 수평거리 sqrt(x*x+z*z), DontCare면 NULL(valid_3d=false여도 일반 객체는 계산)", "m"),
        _f("bbox_height", DoubleType(), False, "2D 박스 높이 bbox_bottom - bbox_top", "px"),
        _f("eligible_easy", BooleanType(), False, "KITTI Easy 적격 (h≥40, occ≤0, trunc≤0.15)"),
        _f("eligible_moderate", BooleanType(), False, "KITTI Moderate 적격 (h≥25, occ≤1, trunc≤0.30)"),
        _f("eligible_hard", BooleanType(), False, "KITTI Hard 적격 (h≥25, occ≤2, trunc≤0.50)"),
        _f("diff_level", IntegerType(), False, "1 Easy, 2 Moderate, 3 Hard, 4 해당 없음"),
        _f("points_in_box", IntegerType(), True, "3D 박스 내부 LiDAR 포인트 수, 무효 3D면 NULL", "count"),
        _f("dist_bin", StringType(), True, "거리 구간 '0-10','10-20','20-40','40+' (DB fn_dist_bin), dist_m이 NULL이면 NULL"),
    ]
)

GRID_STATS = StructType(
    [
        _f("frame_id", IntegerType(), False, "장면 ID"),
        _f("grid_x", IntegerType(), False, "floor((x-0)/1.0), Velodyne x ∈ [0,80) → 0..79", "cell"),
        _f("grid_y", IntegerType(), False, "floor((y+40)/1.0), Velodyne y ∈ [-40,40) → 0..79", "cell"),
        _f("n_points", LongType(), False, "격자 안 포인트 수", "count"),
        _f("z_sum", DoubleType(), False, "격자 안 Velodyne z 합(float64)", "m"),
        _f("z_min", DoubleType(), False, "격자 안 Velodyne z 최솟값", "m"),
        _f("z_max", DoubleType(), False, "격자 안 Velodyne z 최댓값", "m"),
    ]
)

ALL_SCHEMAS: dict[str, StructType] = {
    "frames": FRAMES,
    "objects": OBJECTS,
    "points": POINTS,
    "object_features": OBJECT_FEATURES,
    "grid_stats": GRID_STATS,
}
