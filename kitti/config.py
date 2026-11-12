"""경로, 고정 상수, Java 17 설정, SparkSession 팩토리.

고정값은 `.omc/autopilot/spec.md`(README "구현 시 확정")를 따른다.
- 격자 좌표계: Velodyne 좌표(x 전방, y 좌측, 원점 = 센서), 단위 m
- 관심 영역(ROI): x ∈ [0, 80), y ∈ [-40, 40)
- 격자 크기: 1.0 m → 80 × 80 격자
- 박스 경계 여유 ε = 1e-6 m
- Spark 파티션 수: 32
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from pyspark.sql import SparkSession

# --------------------------------------------------------------------------
# 경로
# --------------------------------------------------------------------------
KITTI_ROOT: Path = Path(os.environ.get("KITTI_ROOT", "~/data/kitti")).expanduser()
"""KITTI 원본 루트. 아래에 training/{image_2,velodyne,label_2,calib}가 있다."""

KITTI_WORK: Path = Path(os.environ.get("KITTI_WORK", "~/data/kitti/work")).expanduser()
"""큰 산출물(Parquet 등)을 쓰는 작업 디렉터리. 저장소 밖에 둔다."""

TRAINING_SUBDIR: str = "training"

# 하위 폴더 이름 → 파일 확장자
SUBDIRS: dict[str, str] = {
    "image_2": ".png",
    "velodyne": ".bin",
    "label_2": ".txt",
    "calib": ".txt",
}

EXPECTED_TRAINING_FRAMES: int = 7481

# --------------------------------------------------------------------------
# 격자·ROI (Velodyne 좌표, m)
# --------------------------------------------------------------------------
ROI_X_MIN: float = 0.0
ROI_X_MAX: float = 80.0  # 미포함
ROI_Y_MIN: float = -40.0
ROI_Y_MAX: float = 40.0  # 미포함
GRID_CELL: float = 1.0
GRID_NX: int = int(round((ROI_X_MAX - ROI_X_MIN) / GRID_CELL))  # 80
GRID_NY: int = int(round((ROI_Y_MAX - ROI_Y_MIN) / GRID_CELL))  # 80

# --------------------------------------------------------------------------
# 판정 상수
# --------------------------------------------------------------------------
EPS: float = 1e-6
"""3D 박스 경계 여유(m). Spark·순차 처리에 똑같이 적용한다."""

N_PARTITIONS: int = 32
"""Spark 셔플·입력 파티션 수(F-12 고정값)."""

# F-2 범위 검사 기준 (spec Critic 8)
POINT_ABS_MAX: float = 200.0
"""포인트 x, y, z 절댓값 상한(m). 넘으면 비정상 좌표."""

# F-7 거리 구간 (DB fn_dist_bin과 동일): <10, <20, <40, >=40 m
DIST_BIN_EDGES: tuple[float, float, float] = (10.0, 20.0, 40.0)
DIST_BIN_LABELS: tuple[str, str, str, str] = ("0-10", "10-20", "20-40", "40+")

# --------------------------------------------------------------------------
# KITTI 라벨
# --------------------------------------------------------------------------
KITTI_CLASSES: tuple[str, ...] = (
    "Car",
    "Van",
    "Truck",
    "Pedestrian",
    "Person_sitting",
    "Cyclist",
    "Tram",
    "Misc",
    "DontCare",
)
DONTCARE: str = "DontCare"

# DontCare 줄의 무효 표식 (원본 값 그대로 보존하고 is_dontcare/valid_3d로 구분)
DONTCARE_TRUNCATED: float = -1.0
DONTCARE_OCCLUDED: int = -1
DONTCARE_ANGLE: float = -10.0  # alpha, rotation_y
DONTCARE_DIM: float = -1.0  # h, w, l
DONTCARE_LOCATION: float = -1000.0  # x, y, z

# KITTI 공식 난이도 기준 (Easy, Moderate, Hard)
DIFF_MIN_BBOX_HEIGHT: tuple[float, float, float] = (40.0, 25.0, 25.0)  # px
DIFF_MAX_OCCLUDED: tuple[int, int, int] = (0, 1, 2)
DIFF_MAX_TRUNCATED: tuple[float, float, float] = (0.15, 0.30, 0.50)
DIFF_NONE: int = 4
"""어떤 난이도에도 해당하지 않는 객체의 diff_level 값."""
DIFF_HEIGHT_COMPARISON: str = ">="
"""2D 박스 높이 기준 비교 연산자. DB 과목 diff_level과 같게 `>=`(경계 포함)를 쓴다(README 요구).
KITTI 공식 devkit 평가 코드는 엄격한 `>`(height > 40/25 px)를 쓰므로, bbox 높이가 정확히
40 px 또는 25 px인 객체는 두 정의에서 등급이 달라진다. 학습 세트에는 그런 실제 객체가 17개 있다."""
DIFF_DEVKIT_EXACT_BOUNDARY_OBJECTS: int = 17
"""학습 세트(DontCare 제외)에서 bbox 높이가 정확히 40 px 또는 25 px인 객체 수(devkit `>`와 차이 나는 대상)."""


def frame_set_info(frame_ids: Iterable[int]) -> dict[str, object]:
    """장면 집합의 기록용 요약: 개수와 sha256(오름차순·중복 제거 frame_id를 ','로 이은 문자열).

    ingest·analysis·sequential이 같은 함수로 기록하고 verify가 양쪽 해시를 비교한다.
    """
    import hashlib

    ids = sorted({int(f) for f in frame_ids})
    digest = hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()
    return {"n": len(ids), "sha256": digest}


def display_path(path: Path | str | None) -> str | None:
    """결과 파일(JSON·MD·CSV)에 남길 경로: 홈 폴더 접두어를 `~`로 바꾼다(사용자 이름을 남기지 않음).

    읽는 쪽은 `Path(p).expanduser()`로 되돌린다. 홈 밖 경로는 그대로 둔다.
    """
    if path is None:
        return None
    text = str(path)
    home = str(Path.home())
    if home not in ("", "/") and (text == home or text.startswith(home + os.sep)):
        return "~" + text[len(home):]
    return text

# --------------------------------------------------------------------------
# Java / Spark
# --------------------------------------------------------------------------
JAVA17_HOME: str = os.environ.get("KITTI_JAVA_HOME", "/usr/lib/jvm/java-17-openjdk-amd64")
SPARK_DRIVER_MEMORY: str = os.environ.get("KITTI_SPARK_DRIVER_MEMORY", "8g")


def training_dir(root: Path | str | None = None) -> Path:
    """`<root>/training` 경로를 돌려준다. root가 없으면 KITTI_ROOT를 쓴다."""
    return Path(root if root is not None else KITTI_ROOT).expanduser() / TRAINING_SUBDIR


def ensure_java17(java_home: str = JAVA17_HOME) -> str:
    """현재 프로세스의 JAVA_HOME·PATH를 Java 17로 맞춘다.

    Spark 4는 Java 17 이상이 필요하다. 사용자 셸 설정(~/.bashrc)은 바꾸지 않고
    이 프로세스(와 여기서 띄우는 JVM)의 환경 변수만 바꾼다.

    Returns:
        설정된 JAVA_HOME 경로.

    Raises:
        FileNotFoundError: java 실행 파일이 없을 때.
    """
    java_bin = Path(java_home) / "bin" / "java"
    if not java_bin.exists():
        raise FileNotFoundError(f"Java 17 not found: {java_bin}")
    os.environ["JAVA_HOME"] = java_home
    bin_dir = str(java_bin.parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if not path_parts or path_parts[0] != bin_dir:
        path_parts = [bin_dir] + [p for p in path_parts if p != bin_dir]
        os.environ["PATH"] = os.pathsep.join(path_parts)
    return java_home


BLAS_THREAD_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)


def set_single_thread_blas() -> None:
    """OMP/OPENBLAS/MKL 스레드 수를 1로 고정한다(측정 공정성, spec Critic 4).

    BLAS 라이브러리는 로드될 때 이 값을 읽으므로 **numpy를 import하기 전에** 호출해야
    효과가 있다. 실행 스크립트 맨 앞에서 `from kitti import config;
    config.set_single_thread_blas()`를 먼저 부른 뒤 kitti.io/geometry를 import한다.
    get_spark()도 JVM을 띄우기 전에 호출하므로 Spark Python worker가 같은 값을 물려받는다.
    """
    for var in BLAS_THREAD_VARS:
        os.environ[var] = "1"


def spark_local_dir() -> Path:
    """Spark 임시 디렉터리 KITTI_WORK/spark-tmp (/tmp는 tmpfs라 디스크로 보낸다). 없으면 만든다."""
    path = KITTI_WORK / "spark-tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_spark(
    master: str = "local[8]",
    app: str = "kitti",
    extra_conf: Mapping[str, str] | None = None,
    ui: bool = False,
) -> "SparkSession":
    """프로젝트 기본 설정으로 SparkSession을 만든다(이미 있으면 재사용).

    기본 설정: UI 끔, 세션 시간대 UTC, shuffle 파티션 32, AQE 끔(파티션 수 고정),
    spark.local.dir = KITTI_WORK/spark-tmp, driver 메모리 `KITTI_SPARK_DRIVER_MEMORY`
    (기본 8g). `extra_conf`가 기본값을 덮어쓴다. spark.driver.memory·spark.local.dir은
    JVM 시작 시에만 반영되므로 이미 세션이 있으면 기존 값이 유지된다.
    """
    ensure_java17()
    set_single_thread_blas()
    # Spark Python worker가 같은 venv 인터프리터를 쓰도록 한다.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    from pyspark.sql import SparkSession

    conf: dict[str, str] = {
        "spark.ui.enabled": "true" if ui else "false",
        "spark.sql.session.timeZone": "UTC",
        "spark.sql.shuffle.partitions": str(N_PARTITIONS),
        "spark.sql.adaptive.enabled": "false",
        "spark.local.dir": str(spark_local_dir()),
        "spark.default.parallelism": str(N_PARTITIONS),
        "spark.driver.memory": SPARK_DRIVER_MEMORY,
        "spark.sql.execution.arrow.pyspark.enabled": "true",
    }
    if extra_conf:
        conf.update({k: str(v) for k, v in extra_conf.items()})

    builder = SparkSession.builder.master(master).appName(app)
    for key, value in conf.items():
        builder = builder.config(key, value)
    return builder.getOrCreate()

