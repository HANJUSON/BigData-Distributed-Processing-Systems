"""S5 처리 결과: 배치가 저장한 결과 파일(JSON·CSV·PNG)만 읽는다 (README 9.3·9.4, 7.2).

웹 요청 중에 Spark를 실행하지 않는다. 숫자는 결과 파일에 있는 값을 그대로 옮기고,
한 줄 결론 문장도 그 값으로 채운다(U-7). 파일이 없으면 그 영역만 빈 상태로 둔다(9.5 부분 성공).

읽는 파일 (results/ 기준)
- quality_report.json (F-1·F-2·D-3), analysis_summary.json (F-5~F-9 요약)
- f7_class_dist.csv · f7_class_occluded.csv · f7_class_difficulty.csv (F-7), f9_thresholds.csv (F-9)
- verification.json, subsets/sample_100·sample_1000/verification.json (F-11, 3개 규모)
- mysql_consistency.json (Parquet ↔ MySQL 적재 대조)
- figures/*.png (F-6·F-7·F-8·F-9 그림)
- bench/summary.csv · bench/environment.json (F-12~F-14, N-3) — 없으면 ‘측정 전’
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .i18n import ko

#: 다운로드·그림으로 내보낼 수 있는 확장자
SERVABLE_SUFFIXES = {".json", ".csv", ".md", ".png"}

DIST_BINS = ["0-10", "10-20", "20-40", "40+"]
DIST_BIN_LABELS = {"0-10": "0–10 m", "10-20": "10–20 m", "20-40": "20–40 m", "40+": "40 m 이상"}

#: (규모, 결과 폴더) — F-11 대조는 3개 규모에서 같은 13개 항목
SCALES: list[tuple[int, str]] = [(100, "subsets/sample_100"), (1000, "subsets/sample_1000"), (7481, ".")]

BENCH_MODES = ["seq", "local1", "local4", "local8"]


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_csv(path: Path) -> list[dict[str, str]] | None:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return None


def _class_order(name: str) -> int:
    keys = list(ko.CLASSES)
    return keys.index(name) if name in keys else len(keys)


def _gb(n: int | None) -> str:
    return "—" if n is None else f"{n / 1e9:,.2f} GB"


def _figure(results: Path, rel: str, title: str, alt: str) -> dict | None:
    return {"path": rel, "title": title, "alt": alt} if (results / rel).is_file() else None


# ---------------------------------------------------------------------------
# 탭별
# ---------------------------------------------------------------------------
def _quality(q: dict | None, summary: dict | None) -> dict | None:
    if q is None:
        return None
    comp, idx, lab, pts = q["completeness"], q["index"], q["labels"], q["points"]
    d3 = q.get("d3_processed", {})
    miss = idx["missing"]
    n_err = lab["object_errors"]["n_objects_with_error"]
    checks = [
        {"item": "4종 파일이 모두 연결된 장면", "count": comp["n_linked"], "total": comp["n_expected"],
         "ok": bool(comp["complete"])},
        {"item": "누락 파일 — 카메라 이미지(image_2)", "count": miss["image_2"]["count"], "ok": miss["image_2"]["count"] == 0},
        {"item": "누락 파일 — LiDAR(velodyne)", "count": miss["velodyne"]["count"], "ok": miss["velodyne"]["count"] == 0},
        {"item": "누락 파일 — 라벨(label_2)", "count": miss["label_2"]["count"], "ok": miss["label_2"]["count"] == 0},
        {"item": "누락 파일 — 보정(calib)", "count": miss["calib"]["count"], "ok": miss["calib"]["count"] == 0},
        {"item": "중복 장면", "count": idx["duplicates"]["count"], "ok": idx["duplicates"]["count"] == 0},
        {"item": "라벨 파싱 오류", "count": lab["parse_errors"]["count"], "ok": lab["parse_errors"]["count"] == 0},
        {"item": "일반 객체 좌표 오류 (NaN·범위 밖)", "count": n_err, "ok": n_err == 0},
        {"item": "무시 영역(DontCare) 행 — 일반 오류와 따로 집계", "count": lab["dontcare"]["count"], "ok": None},
        {"item": "3D 박스 무효 일반 객체 — 포인트 수 계산 안 함", "count": lab["invalid_3d"]["count"], "ok": None},
        {"item": "비정상 LiDAR 포인트 (NaN·범위 밖)", "count": pts["n_points_with_error"], "ok": pts["n_points_with_error"] == 0},
        {"item": "LiDAR 파일 크기 오류", "count": pts["bad_bin_size"]["count"], "ok": pts["bad_bin_size"]["count"] == 0},
        {"item": "보정 파일 파싱 오류", "count": q["calib"]["parse_errors"]["count"], "ok": q["calib"]["parse_errors"]["count"] == 0},
        {"item": "이미지 헤더 오류", "count": q["image"]["header_errors"]["count"], "ok": q["image"]["header_errors"]["count"] == 0},
    ]
    by = d3.get("bytes", {})
    volume = [
        {"item": "처리 용량 합계", "value": _gb(d3.get("bytes_total"))},
        {"item": "카메라 이미지 (image_2)", "value": _gb(by.get("image_2"))},
        {"item": "LiDAR (velodyne)", "value": _gb(by.get("velodyne"))},
        {"item": "라벨 · 보정", "value": f"{_gb(by.get('label_2'))} · {_gb(by.get('calib'))}"},
        {"item": "총 LiDAR 포인트 수", "value": f"{d3['total_points']:,}개" if "total_points" in d3 else "—"},
        {"item": "정답 객체 수 (무시 영역 포함)", "value": f"{lab['n_objects_total']:,}개"},
    ]
    if summary:
        volume.append({"item": "정답 객체 수 (무시 영역 제외)", "value": f"{summary['n_objects_non_dontcare']:,}개"})
    return {
        "conclusion": f"{comp['n_expected']:,}개 장면 중 {comp['n_linked']:,}개 연결, 일반 객체 좌표 오류 {n_err:,}건",
        "source": "quality_report.json",
        "generated_at": q.get("generated_at", "—"),
        "checks": checks,
        "volume": volume,
    }


def _distribution(results: Path, summary: dict | None) -> dict | None:
    rows = _read_csv(results / "f7_class_dist.csv")
    if rows is None:
        return None
    classes = sorted({r["class"] for r in rows}, key=_class_order)
    series = {c: [0] * len(DIST_BINS) for c in classes}
    for r in rows:
        if r["dist_bin"] in DIST_BINS:
            series[r["class"]][DIST_BINS.index(r["dist_bin"])] = int(r["n_objects"])
    occ_rows = _read_csv(results / "f7_class_occluded.csv") or []
    occ = {c: {} for c in classes}
    for r in occ_rows:
        occ.setdefault(r["class"], {})[int(r["occluded"])] = int(r["n_objects"])
    diff_rows = sorted(_read_csv(results / "f7_class_difficulty.csv") or [], key=lambda r: _class_order(r["class"]))
    share = (summary or {}).get("f7", {}).get("share_within_20m_pct", {})
    ped = share.get("Pedestrian")
    return {
        "conclusion": (f"보행자의 {ped:.1f}%가 20 m 이내" if ped is not None
                       else "클래스 × 거리 구간 분포"),
        "source": "f7_class_dist.csv",
        "bins": [DIST_BIN_LABELS[b] for b in DIST_BINS],
        "series": series,
        "share_within_20m": {c: share.get(c) for c in classes},
        "occluded": [{"cls": c, "counts": [occ.get(c, {}).get(k, 0) for k in range(4)]} for c in classes],
        "difficulty": [{"cls": r["class"], "n": int(r["n_objects"]), "easy": int(r["n_eligible_easy"]),
                        "moderate": int(r["n_eligible_moderate"]), "hard": int(r["n_eligible_hard"]),
                        "none": int(r["n_not_eligible"])} for r in diff_rows],
        "figures": [f for f in (
            _figure(results, "figures/f7_class_occluded.png", "클래스 × 가림 정도 (F-7)",
                    "클래스별 가림 정도 0–3 객체 수 누적 막대 그림"),
            _figure(results, "figures/f7_class_difficulty.png", "클래스별 난이도 적격 (F-7)",
                    "클래스별 KITTI 난이도 쉬움·보통·어려움 적격 객체 수 그림"),
            _figure(results, "figures/f7_class_truncated.png", "클래스 × 잘림 구간 (F-7)",
                    "클래스별 잘림 정도 구간 객체 수 그림"),
        ) if f],
    }


def _heatmap(results: Path, summary: dict | None, grid: dict | None) -> dict:
    f6 = (summary or {}).get("f6", {})
    share = f6.get("share_points_x_lt_20m_pct")
    params = []
    if grid:
        roi = grid["roi"]
        params = [
            ("좌표계", "Velodyne (x 앞, y 왼쪽, 원점 = 센서)"),
            ("관심 영역", f"x {roi['x_min']:g}–{roi['x_max_exclusive']:g} m, y {roi['y_min']:g}–{roi['y_max_exclusive']:g} m"),
            ("격자 크기", f"{grid['cell_size_m']:g} m ({grid['grid_shape'][0]} × {grid['grid_shape'][1]})"),
            ("정상 처리 장면 수 (평균 분모)", f"{grid['n_frames_processed']:,}개"),
        ]
    return {
        "conclusion": (f"관심 영역 포인트의 {share:.1f}%가 전방 20 m 이내에 있습니다" if share is not None
                       else "공간 격자 통계"),
        "source": "analysis_summary.json · grid_params.json",
        "params": params,
        "total_points": f6.get("total_points_in_roi"),
        "grid_rows": f6.get("grid_rows"),
        "figures": [f for f in (
            _figure(results, "figures/f6_grid_cumulative.png", "전체 누적 히트맵 (로그 색 척도, 센서 기준 상대 위치)",
                    "모든 장면의 같은 격자를 합산한 LiDAR 포인트 수 히트맵, 로그 색 척도"),
            _figure(results, "figures/f6_sample_scenes.png", "표본 장면 격자 그림",
                    "표본 장면별 격자 포인트 수 그림"),
        ) if f],
    }


def _low_obs(results: Path, summary: dict | None) -> dict | None:
    rows = _read_csv(results / "f9_thresholds.csv")
    if rows is None:
        return None
    f9 = (summary or {}).get("f9", {})
    by_class = f9.get("n_low_by_class", {})
    n_obj, n_scn = f9.get("n_low_objects"), f9.get("n_low_scenes")
    return {
        "conclusion": (f"클래스별 하위 10% 기준으로 객체 {n_obj:,}개, 장면 {n_scn:,}개"
                       if n_obj is not None and n_scn is not None else "클래스별 관측 부족 기준값"),
        "source": "f9_thresholds.csv · analysis_summary.json",
        "rows": [{"cls": r["class"], "n": int(r["n"]), "k": int(r["k"]), "threshold": int(r["threshold"]),
                  "n_low": by_class.get(r["class"])} for r in rows],
        "figures": [f for f in (
            _figure(results, "figures/f9_points_distribution.png", "클래스별 포인트 수 분포와 기준선 (F-9)",
                    "클래스별 3D 박스 내부 포인트 수 분포와 관측 부족 기준선 그림"),
            _figure(results, "figures/f8_visual_check.png", "표본 장면 박스 내부 포인트 시각 확인 (F-8)",
                    "표본 장면에서 3D 박스 안에 들어간 LiDAR 포인트를 표시한 그림"),
        ) if f],
    }


def _verification(results: Path) -> dict | None:
    reports = {scale: _read_json(results / rel / "verification.json") for scale, rel in SCALES}
    if all(r is None for r in reports.values()):
        return None
    items: list[dict[str, Any]] = []
    index: dict[str, dict] = {}
    for scale, _rel in SCALES:
        rep = reports[scale]
        for it in (rep or {}).get("items", []):
            row = index.get(it["name"])
            if row is None:
                row = {"name": it["name"], "title": it["title"], "by_scale": {}}
                index[it["name"]] = row
                items.append(row)
            row["by_scale"][scale] = bool(it["pass"])
    scales = []
    for scale, rel in SCALES:
        rep = reports[scale]
        scales.append({
            "scale": scale,
            "present": rep is not None,
            "pass": None if rep is None else bool(rep["pass"]),
            "n_items": None if rep is None else rep.get("n_items"),
            "frames": None if rep is None else rep.get("frames"),
            "file": str(Path(rel) / "verification.json") if rel != "." else "verification.json",
        })
    all_pass = all(s["present"] and s["pass"] for s in scales)
    full = reports[SCALES[-1][0]] or {}
    return {
        "items": items,
        "scales": scales,
        "all_pass": all_pass,
        "n_items": full.get("n_items", len(items)),
        "tolerance": (full.get("tolerance") or {}).get("rule"),
        "generated_at": full.get("generated_at", "—"),
    }


def _mysql(m: dict | None) -> dict | None:
    if m is None:
        return None
    sections: dict[str, dict[str, int]] = {}
    for c in m["checks"]:
        s = sections.setdefault(c["section"], {"n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += 1 if c["status"] == "ok" else 0
    return {
        "summary": m["summary"],
        "sections": [{"section": k, "label": ko.MYSQL_CHECK_SECTIONS.get(k, k), **v} for k, v in sections.items()],
        "checks": m["checks"],
        "generated_at": m.get("generated_at", "—"),
    }


def _num_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "", "nan", "NaN") else None
    except ValueError:
        return None


def _bench(results: Path) -> dict | None:
    """results/bench/summary.csv가 있으면 표·그래프 값, 없으면 None (‘측정 전’)."""
    rows = _read_csv(results / "bench" / "summary.csv")
    if not rows:
        return None
    table = []
    for r in rows:
        table.append({
            "mode": r["mode"], "label": ko.BENCH_MODE_LABELS.get(r["mode"], r["mode"]),
            "scale": int(r["scale"]), "cores": int(r["cores"]),
            "n_runs": int(r["n_runs"]), "n_exit0": int(r["n_exit0"]), "n_verify_pass": int(r["n_verify_pass"]),
            "wall_median": _num_or_none(r.get("wall_s_median")), "wall_min": _num_or_none(r.get("wall_s_min")),
            "wall_max": _num_or_none(r.get("wall_s_max")),
            "speedup": _num_or_none(r.get("speedup_vs_seq_median")),
            "speedup_min": _num_or_none(r.get("speedup_vs_seq_min")),
            "speedup_max": _num_or_none(r.get("speedup_vs_seq_max")),
            "rss_median": _num_or_none(r.get("peak_rss_mib_median")),
            "rss_min": _num_or_none(r.get("peak_rss_mib_min")),
            "rss_max": _num_or_none(r.get("peak_rss_mib_max")),
        })
    top = max(t["scale"] for t in table)
    speedup = [{"cores": t["cores"], "median": t["speedup"], "lo": t["speedup_min"], "hi": t["speedup_max"]}
               for t in table if t["scale"] == top and t["mode"] != "seq" and t["speedup"] is not None]
    local8 = next((t for t in table if t["scale"] == top and t["mode"] == "local8"), None)
    env = _read_json(results / "bench" / "environment.json")
    env_rows = []
    if env:
        storage = (env.get("storage") or {}).get("data_root") or {}
        env_rows = [
            ("CPU", env.get("cpu_model", "—")),
            ("코어 수", f"물리 {env.get('cpu_physical_cores', '—')} · 논리 {env.get('cpu_logical_cores', '—')}"),
            ("RAM", f"{env.get('ram_total_gib', '—')} GiB"),
            ("저장장치", ", ".join(f"{k}: {v}" for k, v in storage.items()) if isinstance(storage, dict) else str(storage)),
            ("OS", env.get("os", "—")),
            ("Spark / Java", f"{(env.get('versions') or {}).get('pyspark', '—')} / {env.get('java_version', '—')}"),
        ]
    return {
        "table": table,
        "scales": sorted({t["scale"] for t in table}),
        "top_scale": top,
        "speedup": speedup,
        "local8_speedup": None if local8 is None else local8["speedup"],
        "env": env_rows,
    }


def load(results: Path) -> dict:
    """S5 화면 값 전체. 각 탭은 파일이 없으면 None이다."""
    results = Path(results)
    summary = _read_json(results / "analysis_summary.json")
    verification = _verification(results)
    bench = _bench(results)
    perf_conclusion = None
    if verification is not None:
        scales = " · ".join(f"{s['scale']:,}" for s in verification["scales"] if s["present"])
        if verification["all_pass"]:
            perf_conclusion = f"Spark와 순차 처리 결과 {verification['n_items']}개 항목 모두 일치 ({scales}개 장면)"
        else:
            perf_conclusion = "Spark와 순차 처리 결과에 불일치 항목이 있습니다 (아래 표 참고)"
    if bench and bench["local8_speedup"] is not None:
        tail = f"{bench['top_scale']:,}개 장면에서 local[8]이 순차 대비 {bench['local8_speedup']:.2f}배"
        perf_conclusion = f"{perf_conclusion}, {tail}" if perf_conclusion else tail
    elif perf_conclusion:
        perf_conclusion += ". 성능은 측정 전입니다"
    return {
        "generated_at": (summary or {}).get("generated_at", "—"),
        "quality": _quality(_read_json(results / "quality_report.json"), summary),
        "distribution": _distribution(results, summary),
        "heatmap": _heatmap(results, summary, _read_json(results / "grid_params.json")),
        "low_obs": _low_obs(results, summary),
        "verification": verification,
        "mysql": _mysql(_read_json(results / "mysql_consistency.json")),
        "f5_pass": (summary or {}).get("f5_pass"),
        "bench": bench,
        "perf_conclusion": perf_conclusion or "정확성·성능 결과 파일이 아직 없습니다",
    }


def servable(results: Path, rel: str) -> bool:
    """다운로드·그림 경로 검사: results 안의 실제 파일이고 허용한 확장자일 때만."""
    try:
        target = (Path(results) / rel).resolve()
        target.relative_to(Path(results).resolve())
    except (ValueError, OSError):
        return False
    return target.is_file() and target.suffix.lower() in SERVABLE_SUFFIXES
