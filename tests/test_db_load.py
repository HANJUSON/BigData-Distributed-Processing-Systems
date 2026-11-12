"""kitti.db_load 시험: MySQL 없이 가짜 실행기와 가짜 질의 결과로 대조 논리·보고서·종료 코드를 확인한다."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kitti import db_load as dl

DB_DIR = Path(__file__).resolve().parents[1] / "db"

EXPECTED = {
    "row_count": {"frame": 3, "gt_object": 16},
    "null_count": {"gt_object.truncated": 2, "object_feature.points_in_box": 1},
    "threshold": {"Car": 11, "Pedestrian": 19},
    "threshold_n": {"Car": 100, "Pedestrian": 20},
    "contradiction": {"valid_but_null": 0, "dist_m_mismatch": 0},
    "meta": {"note": "비교하지 않음"},
}


def _tsv(overrides: dict[tuple[str, str], str] | None = None, drop: set[tuple[str, str]] | None = None,
         extra: list[tuple[str, str, str]] | None = None) -> str:
    rows: dict[tuple[str, str], str] = {}
    for section in dl.COMPARED_SECTIONS:
        for item, value in EXPECTED[section].items():
            rows[(section, item)] = str(value)
    rows.update(overrides or {})
    for key in drop or set():
        rows.pop(key)
    lines = [f"{s}\t{i}\t{v}" for (s, i), v in rows.items()]
    lines += ["\t".join(e) for e in (extra or [])]
    lines.append("info\tdist_m_max_abs_diff\t0")
    return "\n".join(lines) + "\n"


def _by_key(rows):
    return {(r["section"], r["item"]): r for r in rows}


# ---------------------------------------------------------------- parse / compare
def test_parse_checks() -> None:
    parsed = dl.parse_checks("row_count\tframe\t3\n\nthreshold\tCar\t11\n")
    assert parsed == {"row_count": {"frame": "3"}, "threshold": {"Car": "11"}}
    with pytest.raises(ValueError, match="3 tab-separated"):
        dl.parse_checks("row_count\tframe\n")
    with pytest.raises(ValueError, match="duplicate"):
        dl.parse_checks("row_count\tframe\t3\nrow_count\tframe\t4\n")


def test_compare_all_ok() -> None:
    rows = dl.compare(EXPECTED, dl.parse_checks(_tsv()))
    assert all(r["status"] == "ok" for r in rows)
    assert len(rows) == sum(len(EXPECTED[s]) for s in dl.COMPARED_SECTIONS)
    assert dl.summarize(rows) == {"n_checks": len(rows), "n_ok": len(rows), "n_failed": 0, "passed": True}


def test_compare_detects_each_failure_kind() -> None:
    tsv = _tsv(
        overrides={("row_count", "gt_object"): "15",          # 행 수 불일치
                   ("threshold", "Car"): "12",                 # 기준값 불일치
                   ("contradiction", "valid_but_null"): "3",   # 모순 발생
                   ("null_count", "gt_object.truncated"): "NULL"},  # 빈 테이블의 SUM
        drop={("threshold", "Pedestrian")},                    # MySQL에 기준값 없음
        extra=[("threshold", "Tram", "3")],                     # 기대값에 없는 클래스
    )
    rows = _by_key(dl.compare(EXPECTED, dl.parse_checks(tsv)))
    assert rows[("row_count", "gt_object")]["status"] == "mismatch"
    assert rows[("row_count", "gt_object")]["actual"] == 15
    assert rows[("threshold", "Car")]["status"] == "mismatch"
    assert rows[("contradiction", "valid_but_null")]["status"] == "mismatch"
    assert rows[("null_count", "gt_object.truncated")]["status"] == "mismatch"
    assert rows[("null_count", "gt_object.truncated")]["actual"] == "NULL"
    assert rows[("threshold", "Pedestrian")]["status"] == "missing"
    assert rows[("threshold", "Tram")]["status"] == "unexpected"
    assert rows[("row_count", "frame")]["status"] == "ok"
    summary = dl.summarize(list(rows.values()))
    assert summary["passed"] is False and summary["n_failed"] == 6


def test_compare_ignores_info_and_meta() -> None:
    rows = dl.compare(EXPECTED, dl.parse_checks(_tsv(extra=[("info", "dist_m_exact_mismatch", "4")])))
    assert {r["section"] for r in rows} <= set(dl.COMPARED_SECTIONS)
    assert all(r["status"] == "ok" for r in rows)


def test_compare_integer_like_values() -> None:
    exp = {"row_count": {"frame": 3}}
    assert dl.compare(exp, {"row_count": {"frame": "3.0"}})[0]["status"] == "ok"
    assert dl.compare(exp, {"row_count": {"frame": "3.5"}})[0]["status"] == "mismatch"
    assert dl.compare(exp, {"row_count": {"frame": "abc"}})[0]["status"] == "mismatch"


# ---------------------------------------------------------------- SQL 렌더링
def test_render_sql_replaces_csv_placeholder(tmp_path: Path) -> None:
    csv_dir = tmp_path / "it's csv"
    csv_dir.mkdir()
    sql = dl.render_sql("02_load.sql", csv_dir)
    assert dl.CSV_PLACEHOLDER not in sql
    escaped = str(csv_dir.resolve()).replace("'", "\\'")
    for name in ("frame.csv", "gt_object.csv", "object_feature.csv"):
        assert f"LOCAL INFILE '{escaped}/{name}'" in sql
    with pytest.raises(ValueError, match="csv_dir"):
        dl.render_sql("02_load.sql", None)
    assert dl.render_sql("01_schema.sql") == (DB_DIR / "01_schema.sql").read_text(encoding="utf-8")


def test_sql_files_exist_and_define_required_objects() -> None:
    text = {name: (DB_DIR / name).read_text(encoding="utf-8") for name in dl.SQL_FILES}
    for table in ("frame", "object_class", "gt_object", "object_feature", "analysis_threshold"):
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(", text["01_schema.sql"])
    procs = text["03_views_procs.sql"]
    assert "CREATE OR REPLACE VIEW v_object_obs" in procs
    assert "CREATE FUNCTION fn_dist_bin" in procs
    assert "NTILE(10) OVER (PARTITION BY o.class_id ORDER BY f.points_in_box)" in procs
    code = "\n".join(line.split("--", 1)[0] for line in procs.splitlines())  # 주석 제외
    assert "PERCENT_RANK" not in code
    # DB 과목 제안서 부록의 시그니처와 같아야 한다.
    sig = re.search(r"CREATE PROCEDURE sp_search_scenes\((.*?)\)\s*BEGIN", procs, re.S).group(1)
    params = [" ".join(p.split()) for p in sig.split(",")]
    assert params == [
        "IN p_class TINYINT", "IN p_max_dist DOUBLE", "IN p_occ_min TINYINT", "IN p_occ_max TINYINT",
        "IN p_max_points INT", "IN p_low_only BOOLEAN", "IN p_unreviewed BOOLEAN",
        "IN p_limit INT", "IN p_offset INT",
    ]


def test_fn_dist_bin_labels_match_spark_config() -> None:
    from kitti import config

    procs = (DB_DIR / "03_views_procs.sql").read_text(encoding="utf-8")
    labels = re.findall(r"THEN '([0-9+-]+)'|ELSE '([0-9+-]+)'", procs)
    flat = [a or b for a, b in labels]
    assert tuple(flat) == config.DIST_BIN_LABELS
    edges = tuple(float(e) for e in re.findall(r"WHEN d < (\d+) THEN", procs))
    assert edges == config.DIST_BIN_EDGES


# ---------------------------------------------------------------- run / main
class FakeRunner:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple[str, bool]] = []
        self.local_infile: list[bool] = []

    def __call__(self, sql: str, capture: bool, local_infile: bool = False) -> str:
        self.calls.append((sql, capture))
        self.local_infile.append(local_infile)
        return self.output if capture else ""


@pytest.fixture
def csv_dir(tmp_path: Path) -> Path:
    d = tmp_path / "csv"
    d.mkdir()
    (d / "expected.json").write_text(json.dumps(EXPECTED), encoding="utf-8")
    return d


def test_run_executes_files_in_order_and_writes_reports(csv_dir: Path, tmp_path: Path) -> None:
    fake = FakeRunner(_tsv())
    report = dl.run(csv_dir, csv_dir / "expected.json", tmp_path / "results", fake)
    assert [c[1] for c in fake.calls] == [False, False, False, True]
    assert "CREATE TABLE IF NOT EXISTS frame" in fake.calls[0][0]
    assert str(csv_dir.resolve()) in fake.calls[1][0]
    assert "sp_search_scenes" in fake.calls[2][0]
    assert report["sql_files"] == list(dl.SQL_FILES)
    # --local-infile=1은 LOAD DATA LOCAL INFILE이 있는 02_load.sql에만
    assert fake.local_infile == [False, True, False, False]
    assert "DELETE" not in fake.calls[1][0]  # 기본은 UPSERT만(오래된 행 삭제 없음)
    assert report["replace_stale_rows"] is False
    assert report["summary"]["passed"] is True
    assert report["info"] == {"dist_m_max_abs_diff": "0"}
    saved = json.loads((tmp_path / "results" / "mysql_consistency.json").read_text(encoding="utf-8"))
    assert saved["summary"] == report["summary"]
    md = (tmp_path / "results" / "mysql_consistency.md").read_text(encoding="utf-8")
    assert "**통과**" in md and "| `Car` | 11 | 11 | 일치 |" in md


def test_run_replace_deletes_stale_rows_inside_load_transaction(csv_dir: Path, tmp_path: Path) -> None:
    fake = FakeRunner(_tsv())
    report = dl.run(csv_dir, csv_dir / "expected.json", tmp_path / "results", fake, replace=True)
    load = fake.calls[1][0]
    assert fake.local_infile == [False, True, False, False]
    start, commit = load.index("START TRANSACTION;"), load.rindex("COMMIT;")
    dels = [load.index(f"DELETE {a} FROM {t} {a}") for a, t in
            (("f", "object_feature"), ("o", "gt_object"), ("fr", "frame"))]
    # 모두 트랜잭션 안, UPSERT 뒤, COMMIT 앞. 자식부터(object_feature → gt_object → frame).
    assert start < load.index("INSERT INTO object_feature") < dels[0] < dels[1] < dels[2] < commit
    assert load.count("COMMIT;") == 1
    for stg in ("stg_object_feature", "stg_gt_object", "stg_frame"):
        assert f"LEFT JOIN {stg} s" in load
    assert load.count("WHERE s.frame_id IS NULL;") == 3
    # 다른 파일은 그대로
    assert "DELETE f FROM" not in fake.calls[0][0] and "DELETE f FROM" not in fake.calls[2][0]
    assert report["replace_stale_rows"] is True


def test_with_replace_requires_single_commit() -> None:
    with pytest.raises(ValueError, match="exactly one COMMIT"):
        dl.with_replace("START TRANSACTION;\nSELECT 1;\n")
    with pytest.raises(ValueError, match="exactly one COMMIT"):
        dl.with_replace("START TRANSACTION;\nCOMMIT;\nSTART TRANSACTION;\nCOMMIT;\n")


def test_main_replace_flag(csv_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fakes: list[FakeRunner] = []

    def factory(*a, **k):
        fakes.append(FakeRunner(_tsv()))
        return fakes[-1]

    monkeypatch.setattr(dl, "mysql_runner", factory)
    args = ["--csv-dir", str(csv_dir), "--results", str(tmp_path / "r")]
    assert dl.main(args + ["--replace"]) == 0
    assert "DELETE fr FROM frame fr" in fakes[-1].calls[1][0]
    assert dl.main(args) == 0
    assert "DELETE" not in fakes[-1].calls[1][0]
    with pytest.raises(SystemExit):
        dl.main(args + ["--replace", "--checks-only"])


def test_run_checks_only(csv_dir: Path, tmp_path: Path) -> None:
    fake = FakeRunner(_tsv())
    report = dl.run(csv_dir, csv_dir / "expected.json", tmp_path / "r", fake, checks_only=True)
    assert len(fake.calls) == 1 and fake.calls[0][1] is True
    assert report["sql_files"] == ["04_checks.sql"]


def test_main_exit_codes(csv_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                         capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--csv-dir", str(csv_dir), "--results", str(tmp_path / "r")]

    monkeypatch.setattr(dl, "mysql_runner", lambda *a, **k: FakeRunner(_tsv()))
    assert dl.main(args) == 0

    bad = _tsv(overrides={("contradiction", "dist_m_mismatch"): "2"})
    monkeypatch.setattr(dl, "mysql_runner", lambda *a, **k: FakeRunner(bad))
    assert dl.main(args) == 1
    assert "contradiction/dist_m_mismatch" in capsys.readouterr().err
    md = (tmp_path / "r" / "mysql_consistency.md").read_text(encoding="utf-8")
    assert "**실패**" in md and "**mismatch**" in md

    def failing(*a, **k):
        def run(sql: str, capture: bool) -> str:
            raise dl.MySQLError("Access denied")
        return run

    monkeypatch.setattr(dl, "mysql_runner", failing)
    assert dl.main(args) == 2


def test_mysql_runner_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict = {}

    class Proc:
        returncode = 0
        stdout = "row_count\tframe\t3\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs["input"]
        return Proc()

    monkeypatch.setattr(dl.subprocess, "run", fake_run)
    runner = dl.mysql_runner("mysql", tmp_path / "my.cnf", "kitti")
    assert runner("SELECT 1;", True) == Proc.stdout
    cmd = seen["cmd"]
    assert cmd[0] == "mysql" and cmd[1] == f"--defaults-file={tmp_path / 'my.cnf'}"
    assert "--local-infile=1" not in cmd and "--batch" in cmd and "--skip-column-names" in cmd
    assert "--database=kitti" in cmd
    assert not any(c.startswith(("-p", "--password", "-u", "--user")) for c in cmd)
    assert seen["input"] == "SELECT 1;"

    runner = dl.mysql_runner()
    runner("SELECT 1;", False)
    assert seen["cmd"] == ["mysql", "--default-character-set=utf8mb4"]
    runner("LOAD DATA LOCAL INFILE ...;", False, local_infile=True)
    assert seen["cmd"] == ["mysql", "--local-infile=1", "--default-character-set=utf8mb4"]

    Proc.returncode, Proc.stderr = 1, "ERROR 1045 (28000): Access denied"
    with pytest.raises(dl.MySQLError, match="Access denied"):
        runner("SELECT 1;", False)
