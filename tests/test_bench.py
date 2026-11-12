"""F-12~F-14·N-3·N-4 시험: 합성 KITTI 트리에서 작은 측정(규모 3·5, seq·local1·local2, 2회)을 돌린다.

- bench_raw 행·종료 코드·대조 통과 기록, summary 중앙값, 그림·환경·N-4 판정 파일
- 외부 메모리 측정기가 자식 프로세스 트리(손자 프로세스 포함)의 RSS 합을 잰다
- 예산 초과 시 실행 중단
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import pytest
from PIL import Image

from conftest import CALIB_000000
from kitti import bench, config
from test_sequential_verify import EMPTY_LABEL_FRAME, NO_VELO_FRAME, _label_lines, _points

PROJECT_DIR = Path(__file__).resolve().parents[1]
OBJECTS_NO_VELO_FRAME = 7  # 객체 있음, .bin 없음 → points_in_box NULL, _skipped_frames.json
SCALES = {3: [0, 1, 2], 5: [0, 1, EMPTY_LABEL_FRAME, NO_VELO_FRAME, OBJECTS_NO_VELO_FRAME]}
MODES = ["seq", "local1", "local2"]
REPS = 2


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("kitti_bench")
    base = root / "training"
    for sub in config.SUBDIRS:
        (base / sub).mkdir(parents=True)
    rng = np.random.default_rng(12)
    for fid in sorted({f for ids in SCALES.values() for f in ids}):
        name = f"{fid:06d}"
        Image.new("RGB", (1242, 375)).save(base / "image_2" / f"{name}.png")
        (base / "calib" / f"{name}.txt").write_text(CALIB_000000, encoding="utf-8")
        lines = [] if fid in (EMPTY_LABEL_FRAME, NO_VELO_FRAME) else _label_lines(rng, 10)
        (base / "label_2" / f"{name}.txt").write_text("".join(f"{s}\n" for s in lines), encoding="utf-8")
        if fid not in (NO_VELO_FRAME, OBJECTS_NO_VELO_FRAME):
            _points(rng, 20000).tofile(base / "velodyne" / f"{name}.bin")
    for scale, ids in SCALES.items():
        (root / f"sample_{scale}.txt").write_text("".join(f"{f}\n" for f in ids), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def bench_out(tree: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    work = tmp_path_factory.mktemp("bench_work")
    results = tmp_path_factory.mktemp("bench_results")
    out = bench.run_bench(tree, work, results, list(SCALES), MODES, REPS, samples_dir=tree,
                          partitions=2, warmup_mode="seq", log=lambda *_: None)
    return {**out, "results": results, "work": work}


# --------------------------------------------------------------------------
# 계획
# --------------------------------------------------------------------------
def test_plan_is_shuffled_deterministic_and_complete() -> None:
    a = bench.plan_runs(["seq", "local1", "local4", "local8"], [100, 1000, 7481], 3)
    b = bench.plan_runs(["seq", "local1", "local4", "local8"], [100, 1000, 7481], 3)
    assert a == b
    assert [p["order"] for p in a] == list(range(1, 37))
    combos = {(p["mode"], p["scale"], p["rep"]) for p in a}
    assert len(combos) == 36
    natural = [(m, s, r) for s in (100, 1000, 7481) for m in ("seq", "local1", "local4", "local8") for r in (1, 2, 3)]
    assert [(p["mode"], p["scale"], p["rep"]) for p in a] != natural
    with pytest.raises(ValueError):
        bench.parse_mode("local0")
    assert bench.parse_mode("local8")["master"] == "local[8]"


def test_dry_run_prints_order() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "kitti.bench", "--dry-run", "--scales", "3,5", "--modes", "seq,local2", "--reps", "2"],
        cwd=PROJECT_DIR, capture_output=True, text=True, check=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert sum("warm-up" in ln for ln in lines) == 2
    assert lines[-1] == "total runs=8 seed=42"
    plan = bench.plan_runs(["seq", "local2"], [3, 5], 2)
    first = lines[2].split()
    assert first[0] == "1" and first[1] == plan[0]["mode"]


# --------------------------------------------------------------------------
# 외부 메모리 측정기
# --------------------------------------------------------------------------
GRANDCHILD = (
    "import time; b = bytearray(300 * 1024 * 1024); "
    "[b.__setitem__(i, 1) for i in range(0, len(b), 4096)]; time.sleep(1.5)"
)
CHILD = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {GRANDCHILD!r}], check=True)"


def test_sampler_measures_child_process_tree(tmp_path: Path) -> None:
    mem_csv = tmp_path / "mem.csv"
    res = bench.run_measured([sys.executable, "-c", CHILD], bench.child_env(tmp_path), tmp_path / "log.txt",
                             mem_csv, budget_bytes=None)
    assert res["exit_code"] == 0 and res["abort_reason"] is None
    assert res["peak_rss_mib"] >= 290, res  # 손자 프로세스의 300 MiB가 합에 들어감
    series = pd.read_csv(mem_csv)
    assert list(series.columns) == ["t_s", "rss_mib", "n_procs"]
    assert len(series) == res["mem_samples"] >= 10
    assert series["n_procs"].max() >= 2
    assert series["rss_mib"].max() == pytest.approx(res["peak_rss_mib"], abs=0.01)
    assert np.all(np.diff(series["t_s"]) > 0.05)  # 대략 100 ms 간격


def test_budget_guard_aborts_run(tmp_path: Path) -> None:
    res = bench.run_measured([sys.executable, "-c", CHILD], bench.child_env(tmp_path), tmp_path / "log.txt",
                             tmp_path / "mem.csv", budget_bytes=100 * bench.MIB)
    assert res["exit_code"] != 0
    assert res["abort_reason"] and "budget" in res["abort_reason"]


def test_child_env_single_thread_blas(tmp_path: Path) -> None:
    env = bench.child_env(tmp_path)
    assert all(env[v] == "1" for v in config.BLAS_THREAD_VARS)
    assert env["KITTI_WORK"] == str(tmp_path)
    assert str(PROJECT_DIR) in env["PYTHONPATH"].split(":")


# --------------------------------------------------------------------------
# 작은 측정 전체
# --------------------------------------------------------------------------
def test_bench_raw_rows(bench_out: dict) -> None:
    raw = pd.read_csv(bench_out["results"] / "bench_raw.csv")
    assert list(raw.columns) == list(bench.RAW_COLUMNS)
    assert len(raw) == len(SCALES) * len(MODES) * REPS
    assert sorted(raw["order"]) == list(range(1, len(raw) + 1))
    assert set(zip(raw["mode"], raw["scale"], raw["rep"])) == {
        (m, s, r) for m in MODES for s in SCALES for r in range(1, REPS + 1)
    }
    assert (raw["exit_code"] == 0).all() and (raw["status"] == "ok").all()
    assert raw["verify_pass"].astype(bool).all(), raw[["mode", "scale", "rep", "verify_failed_items"]]
    assert (raw["peak_rss_mib"] > 0).all() and (raw["mem_samples"] > 0).all()
    assert (raw["n_frames"] == raw["scale"]).all()
    # 입력 포인트 수: .bin이 있는 장면만 (규모 3: 0·1·2, 규모 5: 0·1·5)
    assert dict(zip(raw["scale"], raw["n_points"])) == {3: 60000, 5: 60000}
    np.testing.assert_allclose(raw["scenes_per_s"], raw["n_frames"] / raw["wall_s"], rtol=1e-4)
    spark = raw[raw["mode"] != "seq"]
    assert (spark["t_session_s"] > 0).all() and (spark["t_read_compute_s"] > 0).all()
    assert (spark["master"].isin(["local[1]", "local[2]"])).all()
    seq = raw[raw["mode"] == "seq"]
    assert (seq["t_session_s"] == 0).all() and (seq["t_aggregate_s"] == 0).all()
    phase_sum = raw[[f"t_{p}_s" for p in bench.PHASES]].sum(axis=1)
    np.testing.assert_allclose(phase_sum, raw["wall_s"], rtol=1e-4, atol=1e-4)
    # 기준 = 규모별 실행 순서상 첫 seq 반복
    for scale in SCALES:
        first, second = seq[seq["scale"] == scale].sort_values("order").iloc[:2].itertuples()
        refs = raw.loc[raw["scale"] == scale].set_index("order")["verify_ref"]
        assert refs[first.order] == f"seq_{scale}_r{second.rep}"
        assert set(refs.drop(first.order)) == {f"seq_{scale}_r{first.rep}"}
    for rel in raw["mem_csv"]:
        assert (bench_out["results"] / rel).is_file()
    # 실행 폴더는 verify가 읽는 배치
    run_dir = Path(spark.iloc[0]["run_dir"])
    for name in ("object_features", "grid_stats", "f5_dataframe_vs_sql.json", "grid_cumulative.csv",
                 "grid_params.json", "f7_class_dist.csv", "f9_thresholds.csv", "f9_low_objects.csv",
                 "f9_low_scenes.csv", "timings.json"):
        assert (run_dir / name).exists(), name
    big = spark[spark["scale"] == 5].iloc[0]
    skipped = json.loads((Path(big["run_dir"]) / "object_features" / "_skipped_frames.json").read_text())
    assert [f["frame_id"] for f in skipped["frames"]] == [OBJECTS_NO_VELO_FRAME]
    timings = json.loads((run_dir / "timings.json").read_text())
    assert timings["conf"]["spark.sql.adaptive.enabled"] == "false"
    assert timings["blas_threads"]["OMP_NUM_THREADS"] == "1"


def test_warmups_excluded_and_order_recorded(bench_out: dict) -> None:
    res = bench_out["results"]
    warm = pd.read_csv(res / "warmup.csv")
    assert len(warm) == len(SCALES) and (warm["exit_code"] == 0).all()
    order = json.loads((res / "run_order.json").read_text())
    assert order["seed"] == 42
    assert order["runs"] == bench.plan_runs(MODES, list(SCALES), REPS)


def test_summary_medians(bench_out: dict) -> None:
    raw = pd.read_csv(bench_out["results"] / "bench_raw.csv")
    summary = pd.read_csv(bench_out["results"] / "summary.csv")
    assert len(summary) == len(SCALES) * len(MODES)
    seq_med = raw[raw["mode"] == "seq"].groupby("scale")["wall_s"].median()
    for r in summary.itertuples():
        g = raw[(raw["mode"] == r.mode) & (raw["scale"] == r.scale)]
        assert r.n_runs == REPS and r.n_verify_pass == REPS
        assert r.wall_s_median == pytest.approx(g["wall_s"].median())
        assert r.wall_s_min == pytest.approx(g["wall_s"].min())
        assert r.wall_s_max == pytest.approx(g["wall_s"].max())
        assert r.peak_rss_mib_median == pytest.approx(g["peak_rss_mib"].median())
        assert r.t_read_compute_s_median == pytest.approx(g["t_read_compute_s"].median())
        assert r.speedup_vs_seq_median == pytest.approx(seq_med[r.scale] / g["wall_s"].median())
    np.testing.assert_allclose(summary.loc[summary["mode"] == "seq", "speedup_vs_seq_median"], 1.0)


def test_figures_environment_n4(bench_out: dict) -> None:
    res = bench_out["results"]
    for name in ("runtime_vs_scale.png", "speedup.png", "phase_breakdown.png", "peak_rss.png"):
        path = res / "figures" / name
        assert path.is_file() and path.stat().st_size > 5000, name
    env = json.loads((res / "environment.json").read_text())
    for key in ("cpu_model", "cpu_physical_cores", "cpu_logical_cores", "ram_total_mib", "os", "storage",
                "java_version", "versions", "data"):
        assert env[key], key
    assert env["versions"]["pyspark"] and env["versions"]["pyarrow"] and env["versions"]["numpy"]
    assert env["storage"]["data_root"]["fstype"]
    assert env["data"]["training"]["velodyne"] == {"files": 4, "bytes": 4 * 20000 * 16}
    n4 = json.loads((res / "n4_judgment.json").read_text())
    assert n4["pass"] is True and n4["reasons"] == []
    assert n4["expected_runs"] == n4["recorded_runs"] == n4["runs_exit0"] == n4["runs_verify_pass"] == 12
    assert n4["budget_mib"] == pytest.approx(0.8 * psutil.virtual_memory().total / bench.MIB, rel=1e-3)
    assert len(n4["excluded"]) == len(SCALES)
    report = (res / "bench_report.md").read_text()
    assert "N-4 판정: 통과" in report and "공유 페이지" in report and "100 ms" in report


def test_n4_judgment_failures() -> None:
    raw = pd.DataFrame([
        {"order": 1, "mode": "seq", "scale": 3, "rep": 1, "exit_code": 0, "status": "ok", "verify_pass": True,
         "verify_failed_items": "", "peak_rss_mib": 100.0},
        {"order": 2, "mode": "local1", "scale": 3, "rep": 1, "exit_code": 1, "status": "failed",
         "verify_pass": False, "verify_failed_items": "run failed", "peak_rss_mib": 900.0},
    ])
    n4 = bench.n4_judgment(raw, ["seq", "local1"], [3], 2, ram_bytes=1000 * bench.MIB, warmups=[])
    assert n4["pass"] is False
    text = " | ".join(n4["reasons"])
    assert "recorded runs 2 != expected 4" in text
    assert "exit 1" in text and "verify failed" in text and "peak RSS 900 MiB > budget 800 MiB" in text


# --------------------------------------------------------------------------
# 최종 검토 반영
# --------------------------------------------------------------------------
def _rows_for(raw: pd.DataFrame, scale: int, modes: tuple[str, ...], seq_reps: int) -> list[dict]:
    """bench_out 실행 폴더를 다시 써서 verify_runs 입력 행을 만든다(seq는 앞 seq_reps개만)."""
    g = raw[raw["scale"] == scale].sort_values("order")
    seq = g[g["mode"] == "seq"].iloc[:seq_reps]
    other = g[g["mode"].isin(modes)]
    rows = pd.concat([seq, other]).to_dict("records")
    for r in rows:
        r["run_dir"] = str(Path(r["run_dir"]).expanduser())
    return rows


def test_lone_seq_reference_is_cross_verified_against_spark(bench_out: dict, tmp_path: Path) -> None:
    raw = pd.read_csv(bench_out["results"] / "bench_raw.csv")
    rows = _rows_for(raw, 3, ("local1",), seq_reps=1)
    bench.verify_runs(rows, tmp_path)
    ref = next(r for r in rows if r["mode"] == "seq")
    first_spark = min((r for r in rows if r["mode"] == "local1"), key=lambda r: r["order"])
    assert ref["verify_pass"] is True
    assert ref["verify_ref"] == f"local1_3_r{first_spark['rep']} (cross-check of reference)"
    assert (tmp_path / "verify" / f"seq_3_r{ref['rep']}" / "verification.json").is_file()
    assert all(r["verify_pass"] for r in rows)


def test_lone_seq_reference_without_other_run_fails_closed(bench_out: dict, tmp_path: Path) -> None:
    raw = pd.read_csv(bench_out["results"] / "bench_raw.csv")
    rows = _rows_for(raw, 3, (), seq_reps=1)
    bench.verify_runs(rows, tmp_path)
    assert rows[0]["verify_pass"] is False
    assert rows[0]["verify_failed_items"].startswith("reference unverified")
    n4 = bench.n4_judgment(pd.DataFrame(rows), ["seq"], [3], 1, ram_bytes=10**12, warmups=[])
    assert n4["pass"] is False and any("reference unverified" in r for r in n4["reasons"])


def test_speedup_range_uses_seq_min_max() -> None:
    raw = pd.DataFrame([
        {"order": i, "mode": m, "scale": 10, "rep": r, "exit_code": 0, "verify_pass": True, "wall_s": w,
         "peak_rss_mib": 1.0, "scenes_per_s": 1.0, "points_per_s": 1.0,
         **{f"t_{p}_s": 0.0 for p in bench.PHASES}}
        for i, (m, r, w) in enumerate([("seq", 1, 10.0), ("seq", 2, 12.0), ("seq", 3, 14.0),
                                       ("local2", 1, 4.0), ("local2", 2, 5.0), ("local2", 3, 8.0)], start=1)
    ])
    s = bench.summarize(raw).set_index("mode")
    assert s.loc["local2", "speedup_vs_seq_median"] == pytest.approx(12.0 / 5.0)
    assert s.loc["local2", "speedup_vs_seq_min"] == pytest.approx(10.0 / 8.0)
    assert s.loc["local2", "speedup_vs_seq_max"] == pytest.approx(14.0 / 4.0)
    assert s.loc["seq", "speedup_vs_seq_min"] == pytest.approx(10.0 / 14.0)
    assert s.loc["seq", "speedup_vs_seq_max"] == pytest.approx(14.0 / 10.0)


def test_summarize_only_rebuilds_outputs_without_running(bench_out: dict, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    res = tmp_path / "bench"
    shutil.copytree(bench_out["results"], res)
    before = pd.read_csv(res / "summary.csv")
    for name in ("summary.csv", "n4_judgment.json", "bench_report.md"):
        (res / name).unlink()
    shutil.rmtree(res / "figures")
    monkeypatch.setattr(bench, "run_measured", lambda *a, **k: pytest.fail("must not run jobs"))
    monkeypatch.setattr(bench, "verify_runs", lambda *a, **k: pytest.fail("must not re-verify"))
    assert bench.main(["--results", str(res), "--summarize-only"]) == 0
    after = pd.read_csv(res / "summary.csv")
    pd.testing.assert_frame_equal(before, after)
    n4 = json.loads((res / "n4_judgment.json").read_text())
    assert n4["pass"] is True and n4["expected_runs"] == n4["recorded_runs"] == 12
    for name in ("runtime_vs_scale.png", "speedup.png", "phase_breakdown.png", "peak_rss.png"):
        assert (res / "figures" / name).is_file()
    report = (res / "bench_report.md").read_text()
    assert "seq*" in report and "따로 재지 않" in report and "seq 최솟값 / 방식 최댓값" in report


def test_count_points_skips_frames_velodyne_problem_rejects(tmp_path: Path) -> None:
    velo = tmp_path / "training" / "velodyne"
    velo.mkdir(parents=True)
    np.zeros((10, 4), np.float32).tofile(velo / "000000.bin")  # 정상 10점
    (velo / "000001.bin").write_bytes(b"\x00" * 17)  # 16의 배수 아님 → 처리 단계에서 건너뜀
    assert bench.count_points([0, 1, 2], tmp_path) == 10


def test_process_tree_helpers_tolerate_psutil_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(pid):  # noqa: ANN001, ANN202
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(bench.psutil, "Process", denied)
    assert bench._tree(1) == []
    assert bench.tree_rss(1) == (0, 0)

    class Zombie:
        pid = 1

        def kill(self) -> None:
            raise psutil.ZombieProcess(1)

        def memory_info(self):  # noqa: ANN201
            raise psutil.AccessDenied(1)

    monkeypatch.setattr(bench, "_tree", lambda pid: [Zombie()])
    monkeypatch.setattr(bench.psutil, "wait_procs", lambda *a, **k: (_ for _ in ()).throw(psutil.Error()))
    bench.kill_tree(1)  # 예외 없이 끝나야 한다
    assert bench.tree_rss(1) == (0, 0)


def test_raw_and_environment_paths_hide_home(tmp_path: Path) -> None:
    home = Path.home()
    row = {c: None for c in bench.RAW_COLUMNS}
    row.update(order=1, run_dir=str(home / "data" / "w" / "r1"), log=str(tmp_path / "x.log"))
    df = bench._write_raw([row], tmp_path / "raw.csv")
    assert df.loc[0, "run_dir"] == "~/data/w/r1" and df.loc[0, "log"] == str(tmp_path / "x.log")
    assert row["run_dir"] == str(home / "data" / "w" / "r1")  # 대조에 쓰는 행은 절대 경로 그대로
    info = bench.disk_info(home)
    assert info["path"] == "~" and str(home) not in json.dumps(info)
