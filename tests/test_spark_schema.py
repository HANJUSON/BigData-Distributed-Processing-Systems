"""SparkSession 스모크 시험: Java 17·설정값 확인, 스키마별 빈 DataFrame 생성·Parquet 왕복."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from kitti import config


@pytest.fixture(scope="module")
def spark(tmp_path_factory: pytest.TempPathFactory) -> Iterator:
    os.environ["KITTI_WORK"] = str(tmp_path_factory.mktemp("work"))
    # config는 import 시점에 KITTI_WORK를 읽으므로 시험용 경로로 직접 바꾼다.
    config.KITTI_WORK = Path(os.environ["KITTI_WORK"])
    session = config.get_spark("local[2]", app="kitti-test")
    yield session
    session.stop()


def test_spark_session_config(spark) -> None:
    assert os.environ["JAVA_HOME"] == config.JAVA17_HOME
    assert os.environ["OMP_NUM_THREADS"] == "1"
    conf = spark.sparkContext.getConf()
    assert conf.get("spark.ui.enabled") == "false"
    assert conf.get("spark.local.dir") == str(config.KITTI_WORK / "spark-tmp")
    assert spark.conf.get("spark.sql.shuffle.partitions") == "32"
    assert spark.conf.get("spark.sql.adaptive.enabled") == "false"
    assert spark.conf.get("spark.sql.session.timeZone") == "UTC"
    java_version = spark.sparkContext._jvm.java.lang.System.getProperty("java.version")
    assert java_version.startswith("17")


def test_empty_dataframes_for_all_schemas(spark, tmp_path: Path) -> None:
    from kitti import schema

    assert set(schema.ALL_SCHEMAS) == {"frames", "objects", "points", "object_features", "grid_stats"}
    for name, struct in schema.ALL_SCHEMAS.items():
        df = spark.createDataFrame([], struct)
        assert df.count() == 0
        assert df.schema == struct
        out = tmp_path / name
        df.write.parquet(str(out))
        back = spark.read.parquet(str(out))
        assert [f.name for f in back.schema] == [f.name for f in struct]
        assert [f.dataType for f in back.schema] == [f.dataType for f in struct]
        assert all("comment" in f.metadata for f in back.schema)


def test_objects_row_roundtrip(spark, label_file: Path) -> None:
    from kitti import geometry as g
    from kitti import io as kio
    from kitti import schema

    rows = []
    for o in kio.parse_label(label_file):
        row = {k: o[k] for k in schema.OBJECTS.fieldNames() if k in o}
        row["frame_id"] = 1
        row["valid_3d"] = g.is_valid_3d(o["class"], o["h"], o["w"], o["l"], o["z"])
        rows.append(row)
    df = spark.createDataFrame(rows, schema.OBJECTS)
    got = {r["object_id"]: (r["class"], r["is_dontcare"], r["valid_3d"]) for r in df.collect()}
    assert got[4] == ("DontCare", True, False)
    assert got[2] == ("Car", False, True)
    # 라벨 수치는 DoubleType: 문자열에서 파싱한 float64 값이 그대로 보존된다
    first = df.where("object_id = 2").select("bbox_top", "z").first()
    assert (first[0], first[1]) == (181.54, 58.49)


def test_object_features_extends_objects() -> None:
    from pyspark.sql.types import StringType

    from kitti import schema

    n = len(schema.OBJECTS.fields)
    assert schema.OBJECT_FEATURES.fields[:n] == schema.OBJECTS.fields
    assert schema.OBJECT_FEATURES.fieldNames()[n:] == [
        "dist_m", "bbox_height", "eligible_easy", "eligible_moderate", "eligible_hard",
        "diff_level", "points_in_box", "dist_bin",
    ]
    dist_bin = schema.OBJECT_FEATURES["dist_bin"]
    assert dist_bin.dataType == StringType() and dist_bin.nullable
