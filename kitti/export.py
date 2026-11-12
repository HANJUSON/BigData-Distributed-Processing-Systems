"""MySQL 적재용 CSV와 기대값 JSON 내보내기 (README F-10, spec "Critic 반영 결정" 6).

실행:
    python -m kitti.export --work ~/data/kitti/work --out ~/data/kitti/work/mysql_csv
                           [--thresholds results/f9_thresholds.csv] [--quality-report results/quality_report.json]

입력: <work>/parquet/{frames,object_features} (kitti.ingest, kitti.features 출력)
출력 (<out>/):
- frame.csv          : frame_id, image_w, image_h, n_points, quality
- gt_object.csv      : label_2 15개 필드(원본 값 그대로, DontCare 표식 포함) + Spark 파생값
                       (valid_3d, dist_m, diff_level, eligible_*, dist_bin). 파생값은 DB 생성 열과
                       대조하는 데만 쓴다(db/02_load.sql이 stg_gt_object에 넣는다).
- object_feature.csv : DontCare가 아닌 객체의 points_in_box
- expected.json      : MySQL 적재 후 db/04_checks.sql 결과와 비교할 기대값
                       (row_count, null_count, threshold, threshold_n, contradiction)

CSV 형식: UTF-8, 헤더 1줄, 쉼표 구분, 따옴표 없음, NULL = \\N, 줄바꿈 \\n,
불리언 = 1/0, 부동소수 = repr(float)(왕복 가능한 최단 표기). 행은 키 오름차순(결정적).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pyarrow.dataset as ds

from . import config
from . import ingest

NULL: str = "\\N"

FRAME_COLUMNS: tuple[str, ...] = ("frame_id", "image_w", "image_h", "n_points", "quality")

# (CSV 열, object_features 열)
GT_OBJECT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("frame_id", "frame_id"),
    ("object_id", "object_id"),
    ("class_name", "class"),
    ("truncated", "truncated"),
    ("occluded", "occluded"),
    ("alpha", "alpha"),
    ("bbox_left", "bbox_left"),
    ("bbox_top", "bbox_top"),
    ("bbox_right", "bbox_right"),
    ("bbox_bottom", "bbox_bottom"),
    ("dim_h", "h"),
    ("dim_w", "w"),
    ("dim_l", "l"),
    ("loc_x", "x"),
    ("loc_y", "y"),
    ("loc_z", "z"),
    ("rotation_y", "ry"),
    ("valid_3d", "valid_3d"),
    ("dist_m", "dist_m"),
    ("diff_level", "diff_level"),
    ("eligible_easy", "eligible_easy"),
    ("eligible_moderate", "eligible_moderate"),
    ("eligible_hard", "eligible_hard"),
    ("dist_bin", "dist_bin"),
)
OBJECT_FEATURE_COLUMNS: tuple[str, ...] = ("frame_id", "object_id", "points_in_box")

# 적재 때 DontCare면 NULL로 바꾸는 gt_object 열 (db/02_load.sql과 같은 목록)
DONTCARE_NULL_COLUMNS: tuple[str, ...] = (
    "truncated", "occluded", "alpha", "dim_h", "dim_w", "dim_l",
    "loc_x", "loc_y", "loc_z", "rotation_y",
)
BBOX_COLUMNS: tuple[str, ...] = ("bbox_left", "bbox_top", "bbox_right", "bbox_bottom")

# db/04_checks.sql의 contradiction 항목. Spark 쪽 기대값은 모두 0이다.
CONTRADICTION_ITEMS: tuple[str, ...] = (
    "stg_unmapped_class",
    "stg_gt_object_not_loaded",
    "stg_object_feature_not_loaded",
    "points_in_box_mismatch",
    "valid_but_null",
    "invalid_but_value",
    "missing_feature",
    "dontcare_feature",
    "valid_3d_mismatch",
    "diff_level_mismatch",
    "eligible_easy_vs_diff_level",
    "eligible_moderate_vs_diff_level",
    "eligible_hard_vs_diff_level",
    "dist_m_null_mismatch",
    "dist_m_mismatch",
    "dist_bin_mismatch",
)


def format_value(value: Any) -> str:
    """CSV 셀 표기: None → \\N, bool → 1/0, float → repr, 그 밖은 str.

    Raises:
        ValueError: NaN/Inf 부동소수, 또는 쉼표·줄바꿈·역슬래시가 든 문자열(따옴표 없는 CSV라 깨짐).
    """
    if value is None:
        return NULL
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float cannot be exported: {value!r}")
        return repr(value)
    text = str(value)
    if any(ch in text for ch in ",\n\r\\"):
        raise ValueError(f"value contains a delimiter or escape character: {text!r}")
    return text


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=",", lineterminator="\n", quoting=csv.QUOTE_NONE,
                            escapechar=None)
        writer.writerow(header)
        for row in rows:
            writer.writerow([format_value(v) for v in row])
            n += 1
    return n


def _read_table(work: Path, name: str, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
    path = work / "parquet" / name
    if not path.exists():
        raise FileNotFoundError(f"Parquet table not found: {path}")
    table = ds.dataset(str(path), format="parquet").to_table(columns=list(columns) if columns else None)
    return table.to_pylist()


def bad_value_frames(quality_report: Path | None) -> set[int]:
    """품질 보고서(kitti.ingest)에서 비정상 포인트가 있는 장면 ID를 읽는다. 파일이 없으면 빈 집합."""
    if quality_report is None or not Path(quality_report).exists():
        return set()
    report = json.loads(Path(quality_report).read_text(encoding="utf-8"))
    ids = report.get("points", {}).get("frames_with_error", {}).get("ids", [])
    return {int(i) for i in ids}


def frame_quality(row: dict[str, Any], bad_frames: set[int]) -> str:
    """frame.quality ENUM 값: 파일이 하나라도 없거나 포인트 수를 못 읽었으면 missing_file."""
    has_all = all(row.get(k) for k in ("has_image", "has_velodyne", "has_label", "has_calib"))
    if not has_all or row.get("n_points") is None:
        return "missing_file"
    if int(row["frame_id"]) in bad_frames:
        return "bad_value"
    return "ok"


def compute_thresholds(objects: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """클래스별 기준값: 유효 3D 객체 points_in_box를 오름차순 정렬한 ceil(n/10)번째 값 (README F-9).

    MySQL NTILE(10) 1번 구간의 최댓값과 같다(1번 구간 크기 = ceil(n/10)).
    """
    by_class: dict[str, list[int]] = {}
    for o in objects:
        if o["valid_3d"] and not o["is_dontcare"] and o["points_in_box"] is not None:
            by_class.setdefault(o["class"], []).append(int(o["points_in_box"]))
    result: dict[str, dict[str, int]] = {}
    for cls, values in by_class.items():
        values.sort()
        n = len(values)
        k = (n + 9) // 10
        result[cls] = {"n": n, "k": k, "threshold": values[k - 1]}
    return result


def read_spark_thresholds(path: Path | str) -> dict[str, dict[str, int]]:
    """kitti.analysis가 쓴 results/f9_thresholds.csv(class,n,k,threshold)를 읽는다."""
    out: dict[str, dict[str, int]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["class"]] = {k: int(row[k]) for k in ("n", "k", "threshold")}
    return out


def build_expected(
    frames: Sequence[dict[str, Any]],
    objects: Sequence[dict[str, Any]],
    thresholds: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """db/04_checks.sql의 (section, item) 구조와 같은 기대값을 만든다."""
    n_classes = len(config.KITTI_CLASSES)
    non_dc = [o for o in objects if not o["is_dontcare"]]
    n_dc = len(objects) - len(non_dc)

    null_count: dict[str, int] = {
        "frame.image_w": sum(f["image_w"] is None for f in frames),
        "frame.image_h": sum(f["image_h"] is None for f in frames),
        "frame.n_points": sum(f["n_points"] is None for f in frames),
    }
    src = dict(GT_OBJECT_COLUMNS)
    for col in DONTCARE_NULL_COLUMNS:
        null_count[f"gt_object.{col}"] = sum(
            o["is_dontcare"] or o[src[col]] is None for o in objects
        )
    for col in BBOX_COLUMNS:
        null_count[f"gt_object.{col}"] = sum(o[col] is None for o in objects)
    null_count["gt_object.bbox_h"] = sum(o["bbox_height"] is None for o in objects)
    null_count["gt_object.dist_m"] = sum(o["dist_m"] is None for o in objects)
    null_count["gt_object.diff_level"] = sum(o["diff_level"] == config.DIFF_NONE for o in objects)
    # DB valid_3d 생성 열은 DontCare(크기·위치 NULL)에서 NULL이 된다.
    null_count["gt_object.valid_3d"] = n_dc
    null_count["object_feature.points_in_box"] = sum(o["points_in_box"] is None for o in non_dc)

    return {
        "row_count": {
            "frame": len(frames),
            "object_class": n_classes,
            "gt_object": len(objects),
            "object_feature": len(non_dc),
            "analysis_threshold": len(thresholds),
            "stg_frame": len(frames),
            "stg_gt_object": len(objects),
            "stg_object_feature": len(non_dc),
        },
        "null_count": null_count,
        "threshold": {cls: t["threshold"] for cls, t in sorted(thresholds.items())},
        "threshold_n": {cls: t["n"] for cls, t in sorted(thresholds.items())},
        "contradiction": {item: 0 for item in CONTRADICTION_ITEMS},
    }


def export(
    work: Path | str,
    out: Path | str,
    spark_thresholds: Path | str | None = None,
    quality_report: Path | str | None = None,
) -> dict[str, Any]:
    """CSV 3개와 expected.json을 쓰고 기대값 dict를 돌려준다.

    spark_thresholds가 주어지면 그 값(Spark 결과)을 기대값으로 쓰고, Parquet에서 다시 계산한 값과
    다르면 ValueError를 낸다(두 계산이 같은 정의인지 확인).
    """
    work = Path(work).expanduser()
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    frames = sorted(
        _read_table(work, "frames", ("frame_id", "has_image", "has_velodyne", "has_label",
                                      "has_calib", "image_w", "image_h", "n_points")),
        key=lambda r: r["frame_id"],
    )
    objects = sorted(_read_table(work, "object_features"),
                     key=lambda r: (r["frame_id"], r["object_id"]))

    bad = bad_value_frames(Path(quality_report) if quality_report else None)
    n_frame = _write_csv(
        out / "frame.csv", FRAME_COLUMNS,
        ([f["frame_id"], f["image_w"], f["image_h"], f["n_points"], frame_quality(f, bad)]
         for f in frames),
    )
    n_obj = _write_csv(
        out / "gt_object.csv", [c for c, _ in GT_OBJECT_COLUMNS],
        ([o[s] for _, s in GT_OBJECT_COLUMNS] for o in objects),
    )
    n_feat = _write_csv(
        out / "object_feature.csv", OBJECT_FEATURE_COLUMNS,
        ([o["frame_id"], o["object_id"], o["points_in_box"]] for o in objects if not o["is_dontcare"]),
    )

    thresholds = compute_thresholds(objects)
    threshold_source = "parquet"
    if spark_thresholds is not None and Path(spark_thresholds).exists():
        spark = read_spark_thresholds(spark_thresholds)
        if spark != thresholds:
            raise ValueError(
                f"Spark thresholds {spark_thresholds} differ from Parquet recomputation: "
                f"spark={spark} parquet={thresholds}"
            )
        thresholds = spark
        threshold_source = str(spark_thresholds)

    expected = build_expected(frames, objects, thresholds)
    expected["meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "work": str(work),
        "out": str(out),
        "files": {"frame.csv": n_frame, "gt_object.csv": n_obj, "object_feature.csv": n_feat},
        "threshold_source": threshold_source,
        "threshold_k": {cls: t["k"] for cls, t in sorted(thresholds.items())},
        "frame_set": config.frame_set_info(f["frame_id"] for f in frames),
    }
    (out / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return expected


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MySQL 적재용 CSV·기대값 JSON 내보내기 (F-10)")
    parser.add_argument("--work", default=str(config.KITTI_WORK), help="산출물 폴더 (기본 KITTI_WORK)")
    parser.add_argument("--out", default=None, help="CSV 출력 폴더 (기본 <work>/mysql_csv)")
    parser.add_argument("--thresholds", default=str(ingest.RESULTS_DIR / "f9_thresholds.csv"),
                        help="Spark F-9 기준값 CSV. 없으면 Parquet에서 계산한 값만 쓴다")
    parser.add_argument("--quality-report", default=str(ingest.RESULTS_DIR / "quality_report.json"),
                        help="frame.quality=bad_value 판정용 품질 보고서. 없으면 생략")
    args = parser.parse_args(argv)
    work = Path(args.work).expanduser()
    out = Path(args.out).expanduser() if args.out else work / "mysql_csv"
    expected = export(work, out, args.thresholds, args.quality_report)
    files = expected["meta"]["files"]
    print(f"out={out} " + " ".join(f"{k}={v}" for k, v in files.items())
          + f" thresholds={expected['meta']['threshold_source']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
