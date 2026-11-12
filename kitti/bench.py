"""F-12·F-13·F-14·N-3·N-4 성능 측정 실행기. `python -m kitti.bench`.

측정 작업(spec "확정한 값" F-12, Critic 1 방식 A): 장면 목록 → 프레임별 원본 .bin·라벨·보정 읽기 →
F-8 object_features + F-6 장면별 격자 + F-7 분포 + F-9 기준값 → 결과 저장.
- seq: `python -m kitti.sequential` (pyspark import 없음)
- localN: `python -m kitti.bench_job --master local[N]` (32 파티션 mapInArrow + Spark SQL GROUP BY)

실행 규칙 (F-13, spec Critic 3·4)
- 실행마다 새 프로세스. 환경 변수 OMP/OPENBLAS/MKL_NUM_THREADS=1, PYTHONPATH=프로젝트,
  KITTI_WORK=<work> (spark.local.dir = <work>/spark-tmp).
- 외부 측정기: psutil로 100 ms마다 실행 프로세스와 모든 자손(python, java, Python worker)의
  RSS 합을 샘플링해 시계열 CSV(results/bench/mem/)로 저장하고 최댓값을 '샘플링한 프로세스 트리
  peak RSS'(MiB)로 기록한다. 한계: 공유 페이지(라이브러리·fork 후 공유)는 프로세스마다 중복 계산되고,
  100 ms보다 짧은 피크는 놓칠 수 있다.
- 예산 감시: 트리 RSS가 물리 RAM의 80%를 넘으면 그 실행을 중단(트리 전체 kill)하고 실패로 기록한다.
- 실행 직전 시스템 기준선(가용 메모리, load average)을 기록한다.
- 규모마다 예열 실행 1회(기본 방식 = 목록의 마지막 방식)를 측정 전에 돌리고 결과에서 제외한다.
- 측정 실행 순서는 numpy.random.default_rng(42) 순열로 섞고 run_order.json에 기록한다.
- 모든 실행이 끝난 뒤 각 실행 결과를 같은 규모의 기준 순차 결과(실행 순서상 첫 번째로 정상 종료한
  seq 반복)와 kitti.verify로 대조한다(seq 반복도 같은 기준과 대조, 기준 자신은 두 번째 seq 반복과 대조,
  두 번째 seq 반복이 없으면 같은 규모의 첫 정상 Spark 실행과 교차 대조, 그것도 없으면 실패).
  통과 여부를 bench_raw.csv에 남긴다.

출력 (--results, 기본 results/bench)
- bench_raw.csv: 실행 1회 = 1행 (방식, 규모, 반복, 순서, 벽시계 시간, 단계 시간, peak RSS, 처리량,
  대조 통과, 종료 코드, 기준선)
- warmup.csv, run_order.json, mem/*.csv
- summary.csv: 방식 × 규모별 중앙값·최솟값·최댓값, 순차 대비 속도 향상
- environment.json (N-3), n4_judgment.json (N-4), bench_report.md
- figures/: runtime_vs_scale.png, speedup.png, phase_breakdown.png, peak_rss.png

CLI:
    python -m kitti.bench --root ~/data/kitti --work ~/data/kitti/work/bench --results results/bench \
        [--scales 100,1000,7481] [--modes seq,local1,local4,local8] [--reps 3] [--dry-run]
    python -m kitti.bench --results results/bench --summarize-only   # bench_raw.csv에서 요약·그림·보고서만 다시
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata as md
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
import psutil

from . import config

PROJECT_DIR: Path = Path(__file__).resolve().parents[1]
SAMPLES_DIR: Path = PROJECT_DIR / "samples"
DEFAULT_RESULTS: Path = PROJECT_DIR / "results" / "bench"
SEED: int = 42
SAMPLE_INTERVAL_S: float = 0.1
BUDGET_FRACTION: float = 0.8
MIB: int = 1024 * 1024

PHASES: tuple[str, ...] = ("session", "read_compute", "aggregate", "write", "other")
PHASE_LABELS: dict[str, str] = {
    "session": "session / JVM start",
    "read_compute": "read + compute (frame tasks)",
    "aggregate": "aggregate",
    "write": "write results",
    "other": "other (interpreter, imports, shutdown)",
}
# dataviz 기본 범주 팔레트(고정 순서)
PALETTE: tuple[str, ...] = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")

RAW_COLUMNS: tuple[str, ...] = (
    "order", "mode", "master", "scale", "rep", "status", "exit_code", "wall_s",
    "t_session_s", "t_read_compute_s", "t_aggregate_s", "t_write_s", "t_inproc_total_s", "t_other_s",
    "peak_rss_mib", "mem_samples", "mem_csv", "n_frames", "n_points", "scenes_per_s", "points_per_s",
    "verify_pass", "verify_ref", "verify_failed_items",
    "baseline_mem_total_mib", "baseline_mem_available_mib", "baseline_load1", "baseline_load5",
    "baseline_load15", "started_at", "run_dir", "log",
)
LIMITATIONS: tuple[str, ...] = (
    "peak RSS는 100 ms 간격으로 샘플링한 프로세스 트리(실행 Python, JVM, Python worker 등 모든 자손)의 "
    "RSS 합의 최댓값이다. 공유 페이지(공유 라이브러리, fork 후 copy-on-write 페이지)는 프로세스마다 "
    "중복 계산되므로 실제 물리 사용량보다 클 수 있고, 100 ms보다 짧은 피크는 놓칠 수 있다.",
    "페이지 캐시(OS 파일 캐시)는 RSS에 포함되지 않는다. 규모마다 예열 실행 1회로 캐시를 데운 뒤 측정했다.",
    "단계 시간은 프로세스 안에서 잰다. Spark의 read_compute는 격자 작업 시작부터 마지막 프레임 작업 종료까지의 "
    "벽시계 시간이고, aggregate에는 최종 GROUP BY·grid_stats 쓰기와 F-5/F-6/F-7/F-9 질의가 들어간다. "
    "순차 처리의 aggregate는 0으로 두고 집계 시간은 read_compute(=timings.json read+compute)에 포함된다. "
    "other = 벽시계 시간 − 단계 합(인터프리터 시작, import, spark.stop()/JVM 종료).",
    "단계 비교 한계: 순차 실행은 집계를 따로 재지 않았으므로(측정 당시 timings.json에 read·compute·write만 있음) "
    "phase_breakdown.png와 단계 표의 seq read_compute에는 F-5/F-6/F-7/F-9 집계가 들어 있고 seq aggregate = 0은 "
    "'집계 없음'이 아니라 '따로 재지 않음'이다. 따라서 seq와 Spark의 read_compute·aggregate 막대는 단계별로 "
    "1:1 비교할 수 없고, 비교는 벽시계 시간(또는 read_compute + aggregate 합)으로 한다. 다시 측정하지 않고는 "
    "seq 집계 시간을 분리할 수 없어 값은 그대로 두고 그림·표에 주석만 달았다.",
    "속도 향상 범위: 중앙값 비는 seq 중앙값 / 방식 중앙값이고, [최솟값–최댓값]은 seq 최솟값 / 방식 최댓값 .. "
    "seq 최댓값 / 방식 최솟값(반복 간 가장 불리한 짝과 가장 유리한 짝)이다.",
)
SEQ_PHASE_NOTE: str = ("* seq: aggregate not timed separately - F-5/F-6/F-7/F-9 aggregation is included in "
                       "read + compute (no re-run); compare totals, not phases")


# --------------------------------------------------------------------------
# 계획
# --------------------------------------------------------------------------
def parse_mode(mode: str) -> dict[str, Any]:
    """'seq' 또는 'localN' → {mode, kind, cores, master}."""
    if mode == "seq":
        return {"mode": mode, "kind": "seq", "cores": 1, "master": ""}
    m = re.fullmatch(r"local(\d+)", mode)
    if not m or int(m.group(1)) < 1:
        raise ValueError(f"unknown mode '{mode}' (use seq or localN)")
    n = int(m.group(1))
    return {"mode": mode, "kind": "spark", "cores": n, "master": f"local[{n}]"}


def plan_runs(modes: Sequence[str], scales: Sequence[int], reps: int, seed: int = SEED) -> list[dict[str, Any]]:
    """모든 (방식, 규모, 반복)을 seed 순열로 섞은 실행 계획. order는 1부터."""
    for m in modes:
        parse_mode(m)
    combos = [(m, int(s), r) for s in scales for m in modes for r in range(1, reps + 1)]
    perm = np.random.default_rng(seed).permutation(len(combos))
    return [
        {"order": i + 1, "mode": combos[j][0], "scale": combos[j][1], "rep": combos[j][2]}
        for i, j in enumerate(perm)
    ]


def frames_file_for(scale: int, samples_dir: Path) -> Path:
    return Path(samples_dir) / f"sample_{scale}.txt"


def count_points(frame_ids: Sequence[int], root: Path | str) -> int:
    """.bin 파일 크기로 센 입력 포인트 수(모든 방식 공통 입력).

    features.velodyne_problem()이 받아들이는 장면(.bin이 있고 크기가 16바이트 배수)만 센다.
    처리 단계에서 건너뛰는 장면의 포인트를 처리량 분자에 넣지 않기 위해서다.
    """
    from . import features as kf
    from . import io as kio

    total = 0
    for fid in frame_ids:
        if kf.velodyne_problem(root, fid) is not None:
            continue
        total += kio.frame_path(root, "velodyne", fid).stat().st_size // 16
    return total


# --------------------------------------------------------------------------
# 측정
# --------------------------------------------------------------------------
def _tree(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root] + root.children(recursive=True)
    except psutil.Error:  # NoSuchProcess, AccessDenied, ZombieProcess 등
        return []


def tree_rss(pid: int) -> tuple[int, int]:
    """(프로세스 트리 RSS 합 bytes, 프로세스 수)."""
    total, n = 0, 0
    for p in _tree(pid):
        try:
            total += p.memory_info().rss
            n += 1
        except psutil.Error:
            continue
    return total, n


def kill_tree(pid: int) -> None:
    procs = _tree(pid)
    for p in reversed(procs):
        try:
            p.kill()
        except psutil.Error:
            pass
    try:
        psutil.wait_procs(procs, timeout=10)
    except psutil.Error:
        pass


class MemorySampler(threading.Thread):
    """interval마다 프로세스 트리 RSS 합을 기록한다. budget을 넘거나 timeout이 지나면 트리를 죽인다."""

    def __init__(self, pid: int, interval: float = SAMPLE_INTERVAL_S, budget_bytes: int | None = None,
                 timeout_s: float | None = None) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.budget = budget_bytes
        self.timeout = timeout_s
        self.samples: list[tuple[float, int, int]] = []  # (t_s, rss_bytes, n_procs)
        self.abort_reason: str | None = None
        self._halt = threading.Event()
        self._t0 = time.perf_counter()

    def run(self) -> None:
        next_t = time.perf_counter()
        while not self._halt.is_set():
            rss, n = tree_rss(self.pid)
            t = time.perf_counter() - self._t0
            if n:
                self.samples.append((t, rss, n))
            if self.budget is not None and rss > self.budget and self.abort_reason is None:
                self.abort_reason = f"tree RSS {rss / MIB:.0f} MiB > budget {self.budget / MIB:.0f} MiB"
                kill_tree(self.pid)
            elif self.timeout is not None and t > self.timeout and self.abort_reason is None:
                self.abort_reason = f"timeout {self.timeout:.0f} s"
                kill_tree(self.pid)
            next_t += self.interval
            self._halt.wait(max(0.0, next_t - time.perf_counter()))

    def stop(self) -> None:
        self._halt.set()
        self.join()

    @property
    def peak_mib(self) -> float:
        return max((s[1] for s in self.samples), default=0) / MIB

    def write_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["t_s", "rss_mib", "n_procs"])
            for t, rss, n in self.samples:
                w.writerow([f"{t:.3f}", f"{rss / MIB:.3f}", n])


def child_env(work: Path) -> dict[str, str]:
    env = dict(os.environ)
    for var in config.BLAS_THREAD_VARS:
        env[var] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_DIR)] + [p for p in [env.get("PYTHONPATH")] if p])
    env["KITTI_WORK"] = str(work)
    env["PYSPARK_PYTHON"] = sys.executable
    env["PYSPARK_DRIVER_PYTHON"] = sys.executable
    return env


def run_measured(cmd: Sequence[str], env: dict[str, str], log_path: Path, mem_csv: Path,
                 budget_bytes: int | None, timeout_s: float | None = None,
                 interval: float = SAMPLE_INTERVAL_S) -> dict[str, Any]:
    """새 프로세스로 cmd를 실행하며 메모리를 샘플링한다."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        t0 = time.perf_counter()
        proc = subprocess.Popen(list(cmd), cwd=PROJECT_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        sampler = MemorySampler(proc.pid, interval, budget_bytes, timeout_s)
        sampler.start()
        code = proc.wait()
        wall = time.perf_counter() - t0
        sampler.stop()
    # 부모가 끝난 뒤 남은 자손(예: 고아가 된 worker)을 정리한다.
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass
    sampler.write_csv(mem_csv)
    return {
        "exit_code": code,
        "wall_s": wall,
        "peak_rss_mib": sampler.peak_mib,
        "mem_samples": len(sampler.samples),
        "abort_reason": sampler.abort_reason,
    }


def system_baseline() -> dict[str, float]:
    vm = psutil.virtual_memory()
    l1, l5, l15 = os.getloadavg()
    return {
        "baseline_mem_total_mib": round(vm.total / MIB, 1),
        "baseline_mem_available_mib": round(vm.available / MIB, 1),
        "baseline_load1": round(l1, 2),
        "baseline_load5": round(l5, 2),
        "baseline_load15": round(l15, 2),
    }


def job_command(mode: str, root: Path, frames_file: Path, out: Path, partitions: int) -> list[str]:
    m = parse_mode(mode)
    if m["kind"] == "seq":
        return [sys.executable, "-m", "kitti.sequential", "--root", str(root),
                "--frames-file", str(frames_file), "--out", str(out)]
    return [sys.executable, "-m", "kitti.bench_job", "--root", str(root), "--frames-file", str(frames_file),
            "--out", str(out), "--master", m["master"], "--partitions", str(partitions)]


def phase_times(mode: str, run_dir: Path, wall: float) -> dict[str, float | None]:
    """run_dir/timings.json에서 단계 시간을 읽는다. 없으면 None."""
    path = run_dir / "timings.json"
    keys = ("t_session_s", "t_read_compute_s", "t_aggregate_s", "t_write_s", "t_inproc_total_s", "t_other_s")
    if not path.is_file():
        return dict.fromkeys(keys)
    tm = json.loads(path.read_text(encoding="utf-8"))
    ts = tm["timings_s"]
    if parse_mode(mode)["kind"] == "seq":
        ph = {"session": 0.0, "read_compute": ts["read"] + ts["compute"], "aggregate": 0.0, "write": ts["write"]}
    else:
        ph = {k: float(ts[k]) for k in ("session", "read_compute", "aggregate", "write")}
    out = {f"t_{k}_s": round(v, 6) for k, v in ph.items()}
    out["t_inproc_total_s"] = round(float(tm["total_s"]), 6)
    out["t_other_s"] = round(max(0.0, wall - sum(ph.values())), 6)
    return out


# --------------------------------------------------------------------------
# 대조 (N-4)
# --------------------------------------------------------------------------
def _seq_results_view(seq_run: Path, dest: Path) -> Path:
    """순차 출력 폴더를 verify의 'Spark 결과' 자리에 넣기 위한 보기 폴더.

    verify는 Spark 쪽 F-5를 f5_dataframe_vs_sql.json에서 읽으므로 f5_per_class.json을 그 이름으로 연결한다.
    원본 실행 폴더는 바꾸지 않는다.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    links = {p.name: p for p in seq_run.iterdir() if p.is_file()}
    links["f5_dataframe_vs_sql.json"] = seq_run / "f5_per_class.json"
    for name, src in links.items():
        try:
            (dest / name).symlink_to(src)
        except OSError:
            shutil.copy2(src, dest / name)
    return dest


def verify_runs(rows: list[dict[str, Any]], work: Path) -> None:
    """각 실행을 같은 규모의 기준 순차 결과와 대조해 rows를 갱신한다.

    기준 = 실행 순서상 첫 번째로 정상 종료한 seq 반복. 기준 실행 자신은 두 번째 정상 seq 반복과 대조한다.
    같은 규모에 정상 seq 반복이 기준 하나뿐이면 기준을 같은 규모의 첫 정상 Spark 실행과 교차 대조한다
    (Spark 쪽 = 그 Spark 실행, 순차 쪽 = 기준). 그런 실행도 없으면 대조하지 않은 것으로 실패 처리한다.
    """
    from . import verify

    seq_ok: dict[int, list[dict[str, Any]]] = {}
    spark_ok: dict[int, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["order"]):
        if r["exit_code"] == 0:
            (seq_ok if r["mode"] == "seq" else spark_ok).setdefault(r["scale"], []).append(r)
    for r in rows:
        cands = seq_ok.get(r["scale"], [])
        ref = cands[0] if cands else None
        if ref is r:
            # 기준 실행 자신은 같은 규모의 다음 seq 반복과 대조한다(verify는 같은 경로 대조를 거부).
            ref = cands[1] if len(cands) > 1 else None
            if ref is None:
                cross = spark_ok.get(r["scale"], [])
                if not cross:
                    r.update(verify_pass=False, verify_ref="",
                             verify_failed_items="reference unverified: no other successful run at this scale")
                    continue
                other = cross[0]
                name = f"{r['mode']}_{r['scale']}_r{r['rep']}"
                rep = verify.verify(Path(other["run_dir"]), Path(other["run_dir"]), Path(r["run_dir"]))
                verify.write_reports(rep, work / "verify" / name)
                r.update(
                    verify_pass=bool(rep["pass"]),
                    verify_ref=f"{other['mode']}_{other['scale']}_r{other['rep']} (cross-check of reference)",
                    verify_failed_items=";".join(rep["failed_items"]),
                )
                continue
        if r["exit_code"] != 0:
            r.update(verify_pass=False, verify_ref="", verify_failed_items="run failed")
            continue
        if ref is None:
            r.update(verify_pass=False, verify_ref="", verify_failed_items="no successful seq reference")
            continue
        run_dir, ref_dir = Path(r["run_dir"]), Path(ref["run_dir"])
        name = f"{r['mode']}_{r['scale']}_r{r['rep']}"
        results_dir = run_dir
        if r["mode"] == "seq":
            results_dir = _seq_results_view(run_dir, work / "verify" / name / "inputs")
        rep = verify.verify(run_dir, results_dir, ref_dir)
        verify.write_reports(rep, work / "verify" / name)
        r.update(
            verify_pass=bool(rep["pass"]),
            verify_ref=f"seq_{ref['scale']}_r{ref['rep']}",
            verify_failed_items=";".join(rep["failed_items"]),
        )


# --------------------------------------------------------------------------
# 요약·판정
# --------------------------------------------------------------------------
def summarize(raw: pd.DataFrame) -> pd.DataFrame:
    """방식 × 규모별 중앙값·최솟값·최댓값 (정상 종료 실행만으로 시간·메모리 통계)."""
    rows = []
    seq_wall = raw[(raw["mode"] == "seq") & (raw["exit_code"] == 0)].groupby("scale")["wall_s"]
    seq_med, seq_min, seq_max = seq_wall.median(), seq_wall.min(), seq_wall.max()
    for (mode, scale), g in raw.groupby(["mode", "scale"], sort=False):
        ok = g[g["exit_code"] == 0]
        row: dict[str, Any] = {
            "mode": mode, "scale": int(scale), "cores": parse_mode(mode)["cores"],
            "n_runs": len(g), "n_exit0": len(ok), "n_verify_pass": int(g["verify_pass"].astype(bool).sum()),
        }
        for col in ("wall_s", "peak_rss_mib", "scenes_per_s", "points_per_s"):
            row[f"{col}_median"] = ok[col].median() if len(ok) else np.nan
            row[f"{col}_min"] = ok[col].min() if len(ok) else np.nan
            row[f"{col}_max"] = ok[col].max() if len(ok) else np.nan
        for ph in PHASES:
            row[f"t_{ph}_s_median"] = ok[f"t_{ph}_s"].median() if len(ok) else np.nan
        # 중앙값 비 = seq 중앙값 / 방식 중앙값. 범위 = 가장 불리한 짝(seq 최솟값 / 방식 최댓값) ..
        # 가장 유리한 짝(seq 최댓값 / 방식 최솟값).
        row["speedup_vs_seq_median"] = seq_med.get(scale, np.nan) / row["wall_s_median"] if len(ok) else np.nan
        row["speedup_vs_seq_min"] = seq_min.get(scale, np.nan) / row["wall_s_max"] if len(ok) else np.nan
        row["speedup_vs_seq_max"] = seq_max.get(scale, np.nan) / row["wall_s_min"] if len(ok) else np.nan
        rows.append(row)
    out = pd.DataFrame(rows)
    order = {m: i for i, m in enumerate(dict.fromkeys(raw["mode"]))}
    return out.sort_values(["scale", "cores", "mode"], key=lambda c: c.map(order) if c.name == "mode" else c
                           ).reset_index(drop=True)


def n4_judgment(raw: pd.DataFrame, modes: Sequence[str], scales: Sequence[int], reps: int,
                ram_bytes: int, warmups: Sequence[dict[str, Any]]) -> dict[str, Any]:
    expected = len(modes) * len(scales) * reps
    budget_mib = BUDGET_FRACTION * ram_bytes / MIB
    reasons: list[str] = []
    if len(raw) != expected:
        reasons.append(f"recorded runs {len(raw)} != expected {expected}")
    bad_exit = raw[raw["exit_code"] != 0]
    for r in bad_exit.itertuples():
        reasons.append(f"run order {r.order} ({r.mode}, {r.scale}, rep {r.rep}) exit {r.exit_code} [{r.status}]")
    bad_verify = raw[~raw["verify_pass"].astype(bool)]
    for r in bad_verify.itertuples():
        reasons.append(f"run order {r.order} ({r.mode}, {r.scale}, rep {r.rep}) verify failed: "
                       f"{r.verify_failed_items}")
    over = raw[raw["peak_rss_mib"] > budget_mib]
    for r in over.itertuples():
        reasons.append(f"run order {r.order} ({r.mode}, {r.scale}, rep {r.rep}) peak RSS "
                       f"{r.peak_rss_mib:.0f} MiB > budget {budget_mib:.0f} MiB")
    return {
        "criterion": "N-4: all modes x scales x reps runs exit 0, all pass F-11 verify against the same-scale "
                     "sequential reference, sampled process-tree peak RSS <= 80% of physical RAM",
        "expected_runs": expected,
        "recorded_runs": int(len(raw)),
        "runs_exit0": int((raw["exit_code"] == 0).sum()),
        "runs_verify_pass": int(raw["verify_pass"].astype(bool).sum()),
        "ram_total_mib": round(ram_bytes / MIB, 1),
        "budget_mib": round(budget_mib, 1),
        "max_peak_rss_mib": round(float(raw["peak_rss_mib"].max()), 1) if len(raw) else None,
        "excluded": [
            {"mode": w["mode"], "scale": w["scale"], "reason": "warm-up run, excluded from results by design"}
            for w in warmups
        ],
        "scope_note": "This verdict covers scales up to the largest scale measured (7,481 frames in the full run).",
        "pass": not reasons,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------
# 환경 (N-3)
# --------------------------------------------------------------------------
def _version(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def disk_info(path: Path) -> dict[str, Any]:
    """path가 있는 파일 시스템의 장치·형식·장치 모델."""
    real = os.path.realpath(path)
    best = None
    for part in psutil.disk_partitions(all=True):
        mp = part.mountpoint
        if (real == mp or real.startswith(mp.rstrip("/") + "/")) and (best is None or len(mp) > len(best.mountpoint)):
            best = part
    info: dict[str, Any] = {"path": config.display_path(path), "realpath": config.display_path(real)}
    if best is None:
        return info
    info.update(device=best.device, mountpoint=best.mountpoint, fstype=best.fstype)
    try:
        info["free_gib"] = round(shutil.disk_usage(real).free / 2**30, 1)
    except OSError:
        pass
    dev = Path(best.device).name
    m = re.match(r"(nvme\d+n\d+|sd[a-z]+|vd[a-z]+|mmcblk\d+)", dev)
    if m:
        block = Path("/sys/block") / m.group(1)
        for key, rel in (("device_model", "device/model"), ("rotational", "queue/rotational")):
            try:
                info[key] = (block / rel).read_text(encoding="utf-8").strip()
            except OSError:
                pass
    return info


def data_inventory(root: Path) -> dict[str, Any]:
    """데이터 버전 정보: 종류별 파일 수·총 바이트, zip 파일 크기·수정 시각."""
    base = config.training_dir(root)
    kinds = {}
    for sub, ext in config.SUBDIRS.items():
        n, size = 0, 0
        folder = base / sub
        if folder.is_dir():
            with os.scandir(folder) as it:
                for e in it:
                    if e.is_file() and e.name.endswith(ext):
                        n += 1
                        size += e.stat().st_size
        kinds[sub] = {"files": n, "bytes": size}
    zips = []
    zdir = Path(root) / "zips"
    if zdir.is_dir():
        for z in sorted(zdir.glob("*.zip")):
            st = z.stat()
            zips.append({"name": z.name, "bytes": st.st_size,
                         "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))})
    return {
        "dataset": "KITTI 3D object detection, training split",
        "download_dates": "unknown (not recorded at download time); file counts and sizes below identify the copy",
        "training": kinds,
        "zips": zips,
    }


def java_version() -> str | None:
    java = Path(config.JAVA17_HOME) / "bin" / "java"
    try:
        out = subprocess.run([str(java), "-version"], capture_output=True, text=True, timeout=30)
        return (out.stderr or out.stdout).strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _project_relative(path: str) -> str:
    """프로젝트 안 경로는 프로젝트 기준 상대 경로로, 밖이면 홈을 ~로 바꿔 기록한다."""
    try:
        return str(Path(path).relative_to(PROJECT_DIR))
    except ValueError:
        return config.display_path(path) or path


def environment(root: Path, work: Path, partitions: int) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    os_name = platform.platform()
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"') + f" ({platform.platform()})"
    except OSError:
        pass
    return {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpu_model": _cpu_model(),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "ram_total_mib": round(vm.total / MIB, 1),
        "ram_total_gib": round(vm.total / 2**30, 2),
        "os": os_name,
        "kernel": platform.release(),
        "storage": {"data_root": disk_info(root), "work_dir": disk_info(work)},
        "python": sys.version.split()[0],
        "python_executable": _project_relative(sys.executable),
        "versions": {p: _version(p) for p in ("pyspark", "pyarrow", "numpy", "pandas", "psutil", "matplotlib")},
        "java_home": config.JAVA17_HOME,
        "java_version": java_version(),
        "spark_conf": {
            "partitions": partitions,
            "spark.sql.shuffle.partitions": partitions,
            "spark.sql.adaptive.enabled": False,
            "spark.driver.memory": config.SPARK_DRIVER_MEMORY,
            "spark.local.dir": config.display_path(work / "spark-tmp"),
        },
        "blas_threads": {v: "1" for v in config.BLAS_THREAD_VARS},
        "data": data_inventory(root),
    }


# --------------------------------------------------------------------------
# 그림 (F-13, F-14)
# --------------------------------------------------------------------------
def _style(ax) -> None:
    ax.grid(True, color="#e4e3df", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#9a9994")


def make_figures(summary: pd.DataFrame, fig_dir: Path, budget_mib: float) -> list[Path]:
    from .analysis import _plt

    plt = _plt()
    fig_dir.mkdir(parents=True, exist_ok=True)
    modes = list(dict.fromkeys(summary["mode"]))
    scales = sorted(summary["scale"].unique())
    color = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(modes)}
    paths = []

    # 1) 처리 시간 vs 규모
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for m in modes:
        g = summary[summary["mode"] == m].sort_values("scale")
        med = g["wall_s_median"].to_numpy()
        err = np.vstack([med - g["wall_s_min"].to_numpy(), g["wall_s_max"].to_numpy() - med])
        ax.errorbar(g["scale"], med, yerr=err, color=color[m], marker="o", markersize=7, linewidth=2,
                    capsize=4, label=m)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(scales, [f"{s:,}" for s in scales])
    ax.set_xlabel("scenes (frames)")
    ax.set_ylabel("wall time per run (s, log)")
    ax.set_title("Runtime vs scale (median, min–max bars)")
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    paths.append(fig_dir / "runtime_vs_scale.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # 2) 순차 대비 속도 향상 (Spark 방식만, x = 코어 수)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    spark = summary[summary["mode"] != "seq"]
    cores = sorted(spark["cores"].unique()) if len(spark) else [1]
    ax.plot([0, max(cores)], [0, max(cores)], linestyle="--", color="#52514e", linewidth=1.2,
            label="ideal linear (N× sequential)")
    for i, s in enumerate(scales):
        g = spark[spark["scale"] == s].sort_values("cores")
        if not len(g):
            continue
        med = g["speedup_vs_seq_median"].to_numpy()
        err = np.vstack([med - g["speedup_vs_seq_min"].to_numpy(), g["speedup_vs_seq_max"].to_numpy() - med])
        ax.errorbar(g["cores"], med, yerr=err, color=PALETTE[i % len(PALETTE)], marker="o", markersize=7,
                    linewidth=2, capsize=4, label=f"{s:,} scenes")
    ax.axhline(1.0, color="#9a9994", linewidth=0.8)
    ax.set_xticks(cores, [f"local[{c}]" for c in cores])
    ax.set_xlim(0, max(cores) + 0.5)
    top = np.nanmax(np.r_[float(max(cores)), spark["speedup_vs_seq_max"].to_numpy(dtype=float)])
    ax.set_ylim(0, top * 1.08 + 0.2)
    ax.set_ylabel("speedup vs sequential (median wall time ratio)")
    ax.set_title("Speedup over Python sequential (min–max bars)")
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    paths.append(fig_dir / "speedup.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # 3) 단계별 시간 (F-14)
    fig, axes = plt.subplots(1, len(scales), figsize=(4.2 * len(scales), 4.5), squeeze=False)
    for ax, s in zip(axes[0], scales):
        g = summary[summary["scale"] == s].set_index("mode").reindex(modes).dropna(subset=["wall_s_median"])
        bottom = np.zeros(len(g))
        x = np.arange(len(g))
        for j, ph in enumerate(PHASES):
            vals = g[f"t_{ph}_s_median"].fillna(0).to_numpy()
            ax.bar(x, vals, bottom=bottom, width=0.6, color=PALETTE[j], edgecolor="white", linewidth=1,
                   label=PHASE_LABELS[ph] if s == scales[0] else None)
            bottom += vals
        ax.set_xticks(x, [f"{m}*" if m == "seq" else m for m in g.index])
        ax.set_title(f"{s:,} scenes")
        ax.set_ylabel("seconds (median of phase)")
        _style(ax)
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.045), ncol=3, frameon=False, fontsize=8)
    fig.text(0.5, 0.012, SEQ_PHASE_NOTE, ha="center", va="bottom", fontsize=8, color="#52514e")
    fig.suptitle("Phase breakdown per run (F-14)")
    fig.tight_layout(rect=(0, 0.14, 1, 1))
    paths.append(fig_dir / "phase_breakdown.png")
    fig.savefig(paths[-1])
    plt.close(fig)

    # 4) peak RSS
    fig, ax = plt.subplots(figsize=(7, 4.5))
    width = 0.8 / max(1, len(modes))
    for i, m in enumerate(modes):
        g = summary[summary["mode"] == m].set_index("scale").reindex(scales)
        med = g["peak_rss_mib_median"].to_numpy()
        err = np.vstack([med - g["peak_rss_mib_min"].to_numpy(), g["peak_rss_mib_max"].to_numpy() - med])
        x = np.arange(len(scales)) + (i - (len(modes) - 1) / 2) * width
        ax.bar(x, med, width=width * 0.92, color=color[m], label=m, yerr=err, capsize=3,
               error_kw={"elinewidth": 1, "ecolor": "#52514e"})
    ax.set_xticks(np.arange(len(scales)), [f"{s:,}" for s in scales])
    ax.set_xlabel("scenes (frames)")
    ax.set_ylabel("sampled process-tree peak RSS (MiB)")
    ax.set_title(f"Peak RSS (median, min–max); budget = 80% RAM = {budget_mib:,.0f} MiB")
    ax.legend(frameon=False)
    _style(ax)
    fig.tight_layout()
    paths.append(fig_dir / "peak_rss.png")
    fig.savefig(paths[-1])
    plt.close(fig)
    return paths


# --------------------------------------------------------------------------
# 보고서
# --------------------------------------------------------------------------
def render_report(summary: pd.DataFrame, n4: dict[str, Any], env: dict[str, Any],
                  order: dict[str, Any]) -> str:
    lines = [
        "# 성능 측정 결과 (F-12 ~ F-14, N-3, N-4)",
        "",
        f"- 측정 환경: {env['cpu_model']}, 물리 {env['cpu_physical_cores']}코어 / 논리 {env['cpu_logical_cores']}, "
        f"RAM {env['ram_total_gib']} GiB, {env['os']}",
        f"- 저장장치(데이터): {env['storage']['data_root'].get('device')} "
        f"{env['storage']['data_root'].get('fstype')} {env['storage']['data_root'].get('device_model', '')}",
        f"- PySpark {env['versions']['pyspark']}, pyarrow {env['versions']['pyarrow']}, "
        f"numpy {env['versions']['numpy']}, Python {env['python']}",
        f"- 실행 순서: seed {order['seed']} 순열 (run_order.json), 예열 {len(order['warmups'])}회 제외",
        f"- **N-4 판정: {'통과' if n4['pass'] else '실패'}** — 기록 {n4['recorded_runs']}/{n4['expected_runs']}회, "
        f"정상 종료 {n4['runs_exit0']}, 대조 통과 {n4['runs_verify_pass']}, "
        f"최대 peak RSS {n4['max_peak_rss_mib']} MiB / 예산 {n4['budget_mib']} MiB",
        "",
    ]
    if n4["reasons"]:
        lines += ["## 실패 사유", ""] + [f"- {r}" for r in n4["reasons"]] + [""]
    lines += [
        "## 방식 × 규모 요약 (중앙값, [최솟값–최댓값])",
        "",
        "순차 대비 = seq 중앙값 / 방식 중앙값 [seq 최솟값 / 방식 최댓값 – seq 최댓값 / 방식 최솟값].",
        "",
        "| 규모 | 방식 | 실행 | 벽시계 시간 s | 순차 대비 | scenes/s | points/s | peak RSS MiB | 대조 통과 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in summary.itertuples():
        lines.append(
            f"| {r.scale:,} | {r.mode} | {r.n_exit0}/{r.n_runs} | {r.wall_s_median:.2f} "
            f"[{r.wall_s_min:.2f}–{r.wall_s_max:.2f}] | {r.speedup_vs_seq_median:.2f}× "
            f"[{r.speedup_vs_seq_min:.2f}–{r.speedup_vs_seq_max:.2f}] | {r.scenes_per_s_median:.1f} "
            f"| {r.points_per_s_median:,.0f} | {r.peak_rss_mib_median:,.0f} [{r.peak_rss_mib_min:,.0f}–"
            f"{r.peak_rss_mib_max:,.0f}] | {r.n_verify_pass}/{r.n_runs} |"
        )
    lines += [
        "",
        "## 단계별 시간 중앙값 (F-14, 초)",
        "",
        "| 규모 | 방식 | " + " | ".join(PHASE_LABELS[p] for p in PHASES) + " |",
        "| --- | --- | " + " | ".join("---" for _ in PHASES) + " |",
    ]
    for r in summary.itertuples():
        mode = f"{r.mode}*" if r.mode == "seq" else r.mode
        lines.append(f"| {r.scale:,} | {mode} | "
                     + " | ".join(f"{getattr(r, f't_{p}_s_median'):.2f}" for p in PHASES) + " |")
    lines += [
        "",
        "\\* seq: 집계(aggregate)를 따로 재지 않아 F-5/F-6/F-7/F-9 집계 시간이 read + compute에 들어 있다"
        "(아래 '측정 방법과 한계' 참고). 단계별 1:1 비교 대신 합계로 비교한다.",
        "",
        "## 측정 방법과 한계",
        "",
        "- 측정 작업: 장면 목록 → 프레임별 원본 .bin·label_2·calib 읽기 → F-8 박스 내부 포인트 수(object_features) "
        "+ F-6 장면별 격자(grid_stats) + F-7 분포 + F-9 기준값·관측 부족 목록 → 결과 저장. 모든 방식이 같은 입력과 "
        "같은 출력 파일을 만든다.",
        f"- Spark(kitti.bench_job): 장면 목록을 연속 구간 {env['spark_conf']['partitions']}개로 나눠 mapInArrow 한 번으로 .bin을 읽고, 같은 작업에서 "
        "F-8을 numpy 순수 함수로 계산해 작업별 Parquet으로 쓰며 포인트 스트림을 내보낸다. 격자는 그 스트림에 대한 "
        "Spark SQL GROUP BY(CAST AS DOUBLE, NaN·ROI 필터 후 FLOOR)다. "
        f"shuffle 파티션 {env['spark_conf']['spark.sql.shuffle.partitions']}, AQE 끔, "
        f"driver {env['spark_conf']['spark.driver.memory']}, spark.local.dir = "
        f"{config.display_path(env['spark_conf']['spark.local.dir'])} ({env['storage']['work_dir'].get('fstype')}).",
        "- 순차(kitti.sequential): 같은 파일·같은 numpy 함수를 for 루프로 처리, pyspark import 없음.",
        "- 모든 실행은 새 프로세스, OMP/OPENBLAS/MKL_NUM_THREADS=1. 실행 직전 가용 메모리·load average를 기록했다.",
        "- 처리량: scenes/s = 장면 수 / 벽시계 시간, points/s = 입력 .bin 포인트 수 / 벽시계 시간(시작 비용 포함).",
        "- 정확성: 각 실행을 같은 규모의 기준 순차 결과(실행 순서상 첫 정상 종료 seq 반복)와 kitti.verify(F-11)로 "
        "대조했다. 기준 실행 자신은 두 번째 seq 반복과 대조했다(두 번째 seq 반복이 없으면 같은 규모의 첫 정상 "
        "Spark 실행과 교차 대조하고, 그것도 없으면 대조 실패로 기록한다).",
    ] + [f"- {t}" for t in LIMITATIONS] + [
        "",
        "그림: figures/runtime_vs_scale.png, figures/speedup.png, figures/phase_breakdown.png, figures/peak_rss.png",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 전체 실행
# --------------------------------------------------------------------------
def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_raw(rows: list[dict[str, Any]], path: Path) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(RAW_COLUMNS)).sort_values("order").reset_index(drop=True)
    for col in ("run_dir", "log"):  # 홈 경로는 ~로(사용자 이름을 남기지 않음). rows는 절대 경로 그대로 둔다.
        df[col] = df[col].map(lambda v: config.display_path(v) if isinstance(v, str) and v else v)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def run_bench(
    root: Path | str,
    work: Path | str,
    results: Path | str,
    scales: Sequence[int],
    modes: Sequence[str],
    reps: int = 3,
    samples_dir: Path | str = SAMPLES_DIR,
    frames_files: dict[int, Path] | None = None,
    partitions: int = config.N_PARTITIONS,
    warmup_mode: str | None = None,
    seed: int = SEED,
    timeout_s: float | None = None,
    budget_fraction: float = BUDGET_FRACTION,
    log=print,
) -> dict[str, Any]:
    """측정을 모두 실행하고 결과 파일을 쓴다. {raw, summary, n4, environment} 반환."""
    root, work, results = (Path(p).expanduser().resolve() for p in (root, work, results))
    work.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    ffiles = {int(s): Path(frames_files[s]) if frames_files else frames_file_for(int(s), Path(samples_dir))
              for s in scales}
    for s, f in ffiles.items():
        if not f.is_file():
            raise FileNotFoundError(f"frames file for scale {s}: {f}")
    from .features import read_frame_ids

    ids = {s: read_frame_ids(f) for s, f in ffiles.items()}
    n_points = {s: count_points(ids[s], root) for s in ffiles}
    plan = plan_runs(modes, scales, reps, seed)
    warm_mode = warmup_mode or modes[-1]
    warmups = [{"mode": warm_mode, "scale": int(s)} for s in sorted(ffiles)]
    ram = psutil.virtual_memory().total
    budget = int(budget_fraction * ram)
    env = child_env(work)
    order_info = {"seed": seed, "rule": "numpy.random.default_rng(seed).permutation over (scale, mode, rep)",
                  "warmups": warmups, "runs": plan}
    _write_json(results / "run_order.json", order_info)
    environ = environment(root, work, partitions)
    _write_json(results / "environment.json", environ)

    # 예열 (결과에서 제외)
    warm_rows = []
    for w in warmups:
        out = work / "warmup" / w["mode"] / str(w["scale"])
        log(f"[warm-up] {w['mode']} scale={w['scale']}")
        res = run_measured(job_command(w["mode"], root, ffiles[w["scale"]], out, partitions), env,
                           work / "logs" / f"warmup_{w['mode']}_{w['scale']}.log",
                           results / "mem" / f"warmup_{w['mode']}_{w['scale']}.csv", budget, timeout_s)
        warm_rows.append({**w, **res})
    pd.DataFrame(warm_rows).to_csv(results / "warmup.csv", index=False)

    rows: list[dict[str, Any]] = []
    for p in plan:
        mode, scale, rep = p["mode"], p["scale"], p["rep"]
        tag = f"{p['order']:03d}_{mode}_{scale}_r{rep}"
        run_dir = work / mode / str(scale) / f"r{rep}"
        if run_dir.exists():
            shutil.rmtree(run_dir)
        base = system_baseline()
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log(f"[{p['order']}/{len(plan)}] {mode} scale={scale} rep={rep}")
        mem_csv = results / "mem" / f"{tag}.csv"
        log_path = work / "logs" / f"{tag}.log"
        res = run_measured(job_command(mode, root, ffiles[scale], run_dir, partitions), env, log_path, mem_csv,
                           budget, timeout_s)
        status = "ok" if res["exit_code"] == 0 else "failed"
        if res["abort_reason"]:
            status = f"aborted: {res['abort_reason']}"
        wall = res["wall_s"]
        row = {
            **p, "master": parse_mode(mode)["master"], "status": status, "exit_code": res["exit_code"],
            "wall_s": round(wall, 6), **phase_times(mode, run_dir, wall),
            "peak_rss_mib": round(res["peak_rss_mib"], 3), "mem_samples": res["mem_samples"],
            "mem_csv": str(mem_csv.relative_to(results)), "n_frames": len(ids[scale]),
            "n_points": n_points[scale], "scenes_per_s": round(len(ids[scale]) / wall, 6),
            "points_per_s": round(n_points[scale] / wall, 3), "verify_pass": None, "verify_ref": "",
            "verify_failed_items": "", **base, "started_at": started, "run_dir": str(run_dir), "log": str(log_path),
        }
        rows.append(row)
        _write_raw(rows, results / "bench_raw.csv")  # 진행 중에도 저장(대조는 끝에 채움)

    log("[verify] F-11 against same-scale sequential reference")
    verify_runs(rows, work)
    raw = _write_raw(rows, results / "bench_raw.csv")
    summary = summarize(raw)
    summary.to_csv(results / "summary.csv", index=False)
    n4 = n4_judgment(raw, modes, scales, reps, ram, warmups)
    _write_json(results / "n4_judgment.json", n4)
    make_figures(summary, results / "figures", n4["budget_mib"])
    (results / "bench_report.md").write_text(render_report(summary, n4, environ, order_info), encoding="utf-8")
    log(f"[done] N-4 {'PASS' if n4['pass'] else 'FAIL'} -> {results}")
    return {"raw": raw, "summary": summary, "n4": n4, "environment": environ}


def summarize_only(results: Path | str, log=print) -> dict[str, Any]:
    """기존 bench_raw.csv·run_order.json·environment.json에서 summary.csv, n4_judgment.json, 그림,
    bench_report.md만 다시 만든다(실행·대조는 다시 하지 않는다)."""
    results = Path(results).expanduser()
    raw = pd.read_csv(results / "bench_raw.csv")
    order_info = json.loads((results / "run_order.json").read_text(encoding="utf-8"))
    environ = json.loads((results / "environment.json").read_text(encoding="utf-8"))
    runs = order_info["runs"]
    modes = list(dict.fromkeys(r["mode"] for r in runs))
    scales = sorted({int(r["scale"]) for r in runs})
    reps = max(int(r["rep"]) for r in runs)
    ram = int(round(float(environ["ram_total_mib"]) * MIB))
    summary = summarize(raw)
    summary.to_csv(results / "summary.csv", index=False)
    n4 = n4_judgment(raw, modes, scales, reps, ram, order_info["warmups"])
    _write_json(results / "n4_judgment.json", n4)
    make_figures(summary, results / "figures", n4["budget_mib"])
    (results / "bench_report.md").write_text(render_report(summary, n4, environ, order_info), encoding="utf-8")
    log(f"[summarize-only] N-4 {'PASS' if n4['pass'] else 'FAIL'} -> {results}")
    return {"raw": raw, "summary": summary, "n4": n4, "environment": environ}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F-12~F-14 성능 측정 (seq vs Spark local[N])")
    parser.add_argument("--root", default=str(config.KITTI_ROOT), help="KITTI 루트 (training/ 상위)")
    parser.add_argument("--work", default=str(config.KITTI_WORK / "bench"), help="실행 결과·로그·spark-tmp 폴더")
    parser.add_argument("--results", default=str(DEFAULT_RESULTS), help="측정 결과 폴더")
    parser.add_argument("--scales", default="100,1000,7481")
    parser.add_argument("--modes", default="seq,local1,local4,local8")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--samples-dir", default=str(SAMPLES_DIR), help="sample_<scale>.txt 폴더")
    parser.add_argument("--partitions", type=int, default=config.N_PARTITIONS)
    parser.add_argument("--warmup-mode", default=None, help="예열 방식 (기본: --modes의 마지막)")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--timeout", type=float, default=None, help="실행 1회 제한 시간(초)")
    parser.add_argument("--dry-run", action="store_true", help="실행 순서만 출력")
    parser.add_argument("--summarize-only", action="store_true",
                        help="측정하지 않고 --results의 bench_raw.csv에서 요약·판정·그림·보고서만 다시 만든다")
    args = parser.parse_args(argv)
    if args.summarize_only:
        return 0 if summarize_only(args.results)["n4"]["pass"] else 1
    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    plan = plan_runs(modes, scales, args.reps, args.seed)
    if args.dry_run:
        warm = args.warmup_mode or modes[-1]
        for s in sorted(scales):
            print(f"warm-up  {warm:>8} scale={s:>5}  (excluded)")
        for p in plan:
            print(f"{p['order']:>3}  {p['mode']:>8} scale={p['scale']:>5} rep={p['rep']}")
        print(f"total runs={len(plan)} seed={args.seed}")
        return 0
    out = run_bench(args.root, args.work, args.results, scales, modes, args.reps, args.samples_dir,
                    partitions=args.partitions, warmup_mode=args.warmup_mode, seed=args.seed,
                    timeout_s=args.timeout)
    return 0 if out["n4"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
