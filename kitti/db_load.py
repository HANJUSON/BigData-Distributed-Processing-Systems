"""MySQL 적재·대조 (README F-10·F-11, spec "Critic 반영 결정" 6).

실행:
    python -m kitti.db_load --csv-dir ~/data/kitti/work/mysql_csv [--database kitti]
                            [--defaults-file ~/.my.cnf] [--checks-only] [--replace]

--replace(선택): 02_load.sql의 적재 트랜잭션 안(COMMIT 직전)에서 스테이징에 없는 본 테이블 행
(object_feature → gt_object → frame 순)을 지운다. 기본 UPSERT는 이전 적재에만 있던 행을 남기므로,
더 작은 장면 집합으로 다시 적재할 때 쓴다. 다른 테이블(analysis_threshold 등)은 건드리지 않는다.
--local-infile=1은 LOAD DATA LOCAL INFILE이 있는 02_load.sql 실행에만 넘긴다.

순서: db/01_schema.sql → 02_load.sql({{CSV_DIR}} 치환) → 03_views_procs.sql → 04_checks.sql.
04 결과(section, item, value)를 kitti.export가 쓴 expected.json과 비교해
results/mysql_consistency.json·.md에 기록한다.

접속 정보는 코드에 두지 않는다. `mysql` CLI가 옵션 파일(기본 ~/.my.cnf, --defaults-file로 변경)의
[client] user·password·host와 [mysql] database를 읽는다. --database를 주면 그 값이 우선한다.

종료 코드: 0 = 모두 일치, 1 = 불일치, 2 = SQL 실행 실패(mysql 오류).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import config, ingest

DB_DIR: Path = Path(__file__).resolve().parents[1] / "db"
SQL_FILES: tuple[str, ...] = ("01_schema.sql", "02_load.sql", "03_views_procs.sql", "04_checks.sql")
CSV_PLACEHOLDER: str = "{{CSV_DIR}}"
COMPARED_SECTIONS: tuple[str, ...] = ("row_count", "null_count", "threshold", "threshold_n", "contradiction")

LOAD_FILE: str = "02_load.sql"
"""LOAD DATA LOCAL INFILE을 쓰는 유일한 파일(--local-infile=1은 이 파일에만)."""
REPLACE_SQL: str = """
-- --replace: 스테이징(이번 CSV)에 없는 본 테이블 행을 지운다(같은 트랜잭션, COMMIT 직전).
-- 자식부터 지운다(gt_object → frame FK는 ON DELETE RESTRICT).
DELETE f FROM object_feature f
  LEFT JOIN stg_object_feature s ON s.frame_id = f.frame_id AND s.object_id = f.object_id
  WHERE s.frame_id IS NULL;
DELETE o FROM gt_object o
  LEFT JOIN stg_gt_object s ON s.frame_id = o.frame_id AND s.object_id = o.object_id
  WHERE s.frame_id IS NULL;
DELETE fr FROM frame fr
  LEFT JOIN stg_frame s ON s.frame_id = fr.frame_id
  WHERE s.frame_id IS NULL;
"""

# (SQL 문자열, 결과 출력 여부, local_infile=False) → stdout. 시험에서 가짜 실행기로 바꿔 끼운다.
Runner = Callable[..., str]


class MySQLError(RuntimeError):
    """mysql CLI가 0이 아닌 코드로 끝남."""


def sql_string_literal_path(path: Path) -> str:
    """SQL 작은따옴표 문자열 안에 넣을 경로: 역슬래시·작은따옴표를 이스케이프한다."""
    return str(path).replace("\\", "\\\\").replace("'", "\\'")


def render_sql(name: str, csv_dir: Path | None = None, db_dir: Path = DB_DIR) -> str:
    """SQL 파일을 읽고 {{CSV_DIR}}를 절대 경로로 바꾼다. 치환할 값이 없는데 자리표시가 있으면 오류."""
    text = (db_dir / name).read_text(encoding="utf-8")
    if CSV_PLACEHOLDER in text:
        if csv_dir is None:
            raise ValueError(f"{name} needs csv_dir for {CSV_PLACEHOLDER}")
        text = text.replace(CSV_PLACEHOLDER, sql_string_literal_path(Path(csv_dir).expanduser().resolve()))
    return text


def with_replace(load_sql: str) -> str:
    """02_load.sql 본문의 마지막 COMMIT 바로 앞에 REPLACE_SQL을 넣는다(COMMIT이 정확히 하나여야 함)."""
    marker = "\nCOMMIT;"
    if load_sql.count(marker) != 1:
        raise ValueError(f"{LOAD_FILE} must contain exactly one COMMIT; for --replace")
    return load_sql.replace(marker, "\n" + REPLACE_SQL.strip("\n") + "\n" + marker, 1)


def mysql_runner(
    mysql: str = "mysql",
    defaults_file: Path | str | None = None,
    database: str | None = None,
) -> Runner:
    """mysql CLI로 SQL을 stdin에 넘겨 실행하는 Runner를 만든다.

    --defaults-file은 반드시 첫 인자여야 한다(mysql 규칙). 결과를 읽을 때는
    --batch --raw --skip-column-names로 탭 구분 행만 받는다.
    """

    def run(sql: str, capture: bool, local_infile: bool = False) -> str:
        cmd = [mysql]
        if defaults_file is not None:
            cmd.append(f"--defaults-file={Path(defaults_file).expanduser()}")
        if local_infile:
            cmd.append("--local-infile=1")
        cmd.append("--default-character-set=utf8mb4")
        if capture:
            cmd += ["--batch", "--raw", "--skip-column-names"]
        if database:
            cmd.append(f"--database={database}")
        proc = subprocess.run(cmd, input=sql, text=True, capture_output=True, encoding="utf-8")
        if proc.returncode != 0:
            raise MySQLError(f"{' '.join(cmd)} exited {proc.returncode}: {proc.stderr.strip()}")
        return proc.stdout

    return run


def parse_checks(tsv: str) -> dict[str, dict[str, str]]:
    """04_checks.sql 출력(section\\titem\\tvalue 줄)을 {section: {item: value}}로 바꾼다.

    Raises:
        ValueError: 열 수가 3이 아니거나 같은 (section, item)이 두 번 나올 때.
    """
    result: dict[str, dict[str, str]] = {}
    for line_no, line in enumerate(tsv.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"line {line_no}: expected 3 tab-separated columns, got {len(parts)}: {line!r}")
        section, item, value = parts
        bucket = result.setdefault(section, {})
        if item in bucket:
            raise ValueError(f"line {line_no}: duplicate check {section}/{item}")
        bucket[item] = value
    return result


def _as_int(value: str | None) -> int | None:
    if value is None or value == "NULL":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            as_float = float(value)
        except ValueError:
            return None
        return int(as_float) if as_float.is_integer() else None


def compare(expected: dict[str, Any], actual: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """기대값과 MySQL 결과를 비교해 항목별 결과 목록을 돌려준다.

    비교 대상은 COMPARED_SECTIONS의 모든 항목이다. 양쪽 중 한쪽에만 있는 항목도 불일치로 센다
    (예: MySQL에 기준값이 없는 클래스, 기대값에 없는 클래스). 값은 정수로 비교한다.
    """
    rows: list[dict[str, Any]] = []
    for section in COMPARED_SECTIONS:
        exp_items: dict[str, Any] = expected.get(section, {})
        act_items: dict[str, str] = actual.get(section, {})
        for item in sorted(set(exp_items) | set(act_items)):
            exp = exp_items.get(item)
            raw = act_items.get(item)
            act = _as_int(raw)
            if item not in exp_items:
                status = "unexpected"
            elif item not in act_items:
                status = "missing"
            else:
                status = "ok" if act is not None and act == int(exp) else "mismatch"
            rows.append({"section": section, "item": item, "expected": exp,
                         "actual": act if act is not None else raw, "status": status})
    return rows


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    bad = [r for r in rows if r["status"] != "ok"]
    return {"n_checks": len(rows), "n_ok": len(rows) - len(bad), "n_failed": len(bad), "passed": not bad}


def render_markdown(report: dict[str, Any]) -> str:
    """results/mysql_consistency.md 본문."""
    s = report["summary"]
    verdict = "통과" if s["passed"] else "실패"
    lines = [
        "# Spark ↔ MySQL 적재 대조 (F-10·F-11)",
        "",
        f"- 생성: {report['generated_at']}",
        f"- 기대값: `{report['expected_file']}`",
        f"- 판정: **{verdict}** ({s['n_ok']} / {s['n_checks']} 항목 일치, 불일치 {s['n_failed']}건)",
        "",
        "기대값은 `kitti.export`가 Parquet(Spark 결과)에서 계산했고, 실제값은 `db/04_checks.sql` 결과다.",
        "기준값은 클래스별 NTILE(10) 1번 구간 최댓값(= 정렬 후 ceil(n/10)번째 값)이다.",
        "",
    ]
    titles = {
        "row_count": "행 수", "null_count": "NULL 수", "threshold": "클래스별 기준값",
        "threshold_n": "기준값 계산 대상 객체 수", "contradiction": "모순 건수 (0이어야 함)",
    }
    for section in COMPARED_SECTIONS:
        sec_rows = [r for r in report["checks"] if r["section"] == section]
        if not sec_rows:
            continue
        lines += [f"## {titles[section]}", "", "| 항목 | 기대값 | MySQL | 결과 |", "| --- | ---: | ---: | --- |"]
        for r in sec_rows:
            mark = "일치" if r["status"] == "ok" else f"**{r['status']}**"
            exp = "" if r["expected"] is None else r["expected"]
            act = "" if r["actual"] is None else r["actual"]
            lines.append(f"| `{r['item']}` | {exp} | {act} | {mark} |")
        lines.append("")
    if report.get("info"):
        lines += ["## 참고값 (비교하지 않음)", ""]
        lines += [f"- `{k}`: {v}" for k, v in sorted(report["info"].items())]
        lines.append("")
    return "\n".join(lines)


def run(
    csv_dir: Path | str,
    expected_path: Path | str,
    results_dir: Path | str,
    runner: Runner,
    checks_only: bool = False,
    db_dir: Path = DB_DIR,
    replace: bool = False,
) -> dict[str, Any]:
    """SQL 01→04를 실행하고 대조 보고서를 쓴 뒤 보고서 dict를 돌려준다.

    replace=True면 02_load.sql 트랜잭션 안에서 스테이징에 없는 frame·gt_object·object_feature 행을 지운다.
    """
    csv_dir = Path(csv_dir).expanduser()
    expected_path = Path(expected_path).expanduser()
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    executed: list[str] = []
    if not checks_only:
        for name in SQL_FILES[:-1]:
            sql = render_sql(name, csv_dir, db_dir)
            if name == LOAD_FILE:
                runner(with_replace(sql) if replace else sql, False, local_infile=True)
            else:
                runner(sql, False)
            executed.append(name)
    output = runner(render_sql(SQL_FILES[-1], csv_dir, db_dir), True)
    executed.append(SQL_FILES[-1])
    actual = parse_checks(output)

    rows = compare(expected, actual)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "csv_dir": config.display_path(csv_dir),
        "expected_file": config.display_path(expected_path),
        "sql_files": executed,
        "replace_stale_rows": bool(replace and not checks_only),
        "summary": summarize(rows),
        "checks": rows,
        "info": actual.get("info", {}),
    }
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "mysql_consistency.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (results_dir / "mysql_consistency.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MySQL 적재와 Spark↔MySQL 대조 (F-10)")
    parser.add_argument("--csv-dir", required=True, help="kitti.export 출력 폴더")
    parser.add_argument("--expected", default=None, help="기대값 JSON (기본 <csv-dir>/expected.json)")
    parser.add_argument("--results", default=str(ingest.RESULTS_DIR), help="보고서 폴더 (기본 results/)")
    parser.add_argument("--database", default=None, help="DB 이름. 없으면 옵션 파일의 database")
    parser.add_argument("--defaults-file", default=None, help="mysql 옵션 파일 (기본: mysql이 ~/.my.cnf를 읽음)")
    parser.add_argument("--mysql", default="mysql", help="mysql CLI 경로")
    parser.add_argument("--checks-only", action="store_true", help="04_checks.sql만 실행해 대조")
    parser.add_argument("--replace", action="store_true",
                        help="적재 트랜잭션 안에서 이번 CSV(스테이징)에 없는 frame·gt_object·object_feature 행 삭제")
    args = parser.parse_args(argv)
    if args.replace and args.checks_only:
        parser.error("--replace cannot be combined with --checks-only")

    csv_dir = Path(args.csv_dir).expanduser()
    expected = Path(args.expected).expanduser() if args.expected else csv_dir / "expected.json"
    runner = mysql_runner(args.mysql, args.defaults_file, args.database)
    try:
        report = run(csv_dir, expected, args.results, runner, args.checks_only, replace=args.replace)
    except MySQLError as exc:
        print(f"MySQL error: {exc}", file=sys.stderr)
        return 2
    s = report["summary"]
    print(f"checks={s['n_checks']} ok={s['n_ok']} failed={s['n_failed']} "
          f"report={Path(args.results) / 'mysql_consistency.md'}")
    for r in report["checks"]:
        if r["status"] != "ok":
            print(f"  {r['status']}: {r['section']}/{r['item']} expected={r['expected']} actual={r['actual']}",
                  file=sys.stderr)
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
