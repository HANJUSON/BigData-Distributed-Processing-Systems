"""MySQL 서비스 DB 접근 (README 7.2 ‘적재·검색 모듈’, DB 과목 NR-1·NR-4).

- 접속 정보는 코드·저장소에 두지 않는다. MySQL 옵션 파일(기본 ``~/.my.cnf``, 환경변수
  ``KITTI_MYSQL_CNF``로 변경)의 ``[client]`` 절(user·password·host·database)을 PyMySQL의
  ``read_default_file``로 읽는다.
- 요청마다 연결 하나를 열고(처음 쓸 때) 요청이 끝나면 닫는다(``flask.g``).
- 화면은 저장 프로시저만 호출한다. 호출할 수 있는 문장은 아래 ``PROCEDURES``에 고정해 두고,
  값은 모두 자리표시(%s)로 넘긴다. SQL 문자열을 조립하지 않는다(NR-1).
- 오류는 두 종류로 나눠 화면 문구(ko.DB_ERROR_MESSAGES)로 바꾼다(NR-4).
  ``DBUnavailable``: 연결 실패·옵션 파일 없음·접속 거부 → ‘데이터베이스에 연결하지 못했습니다…’
  ``DBQueryError``: 연결은 됐지만 프로시저 실행 실패(프로시저 없음 등) → ‘조회하지 못했습니다…’
  원래 오류 메시지는 ``detail``로 보관해 ‘자세히’ 안에만 보여준다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Protocol

import pymysql
import pymysql.cursors
from flask import current_app, g

DEFAULT_CNF = "~/.my.cnf"
CONNECT_TIMEOUT_S = 3
READ_TIMEOUT_S = 15

#: 화면이 호출하는 프로시저와 그 CALL 문 (db/03_views_procs.sql)
PROCEDURES: dict[str, str] = {
    "sp_search_scenes_ex": "CALL sp_search_scenes_ex(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
    "sp_search_summary": "CALL sp_search_summary(%s, %s, %s, %s, %s, %s, %s, %s)",
    "sp_scene_cards": "CALL sp_scene_cards(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
    "sp_scene_detail": "CALL sp_scene_detail(%s)",
    "sp_thresholds": "CALL sp_thresholds()",
    "sp_class_ids": "CALL sp_class_ids(%s)",
}

ResultSets = list[list[dict[str, Any]]]


class DBError(Exception):
    """DB 오류의 공통 부모. ``kind``는 ko.DB_ERROR_MESSAGES의 키, ``detail``은 원래 메시지."""

    kind = "query_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class DBUnavailable(DBError):
    kind = "db_unavailable"


class DBQueryError(DBError):
    kind = "query_failed"


class Database(Protocol):
    """화면이 쓰는 DB 인터페이스. 시험에서는 가짜 구현으로 바꿔 끼운다."""

    def call(self, proc: str, *args: Any) -> ResultSets: ...

    def close(self) -> None: ...


def cnf_path() -> Path:
    return Path(os.environ.get("KITTI_MYSQL_CNF", DEFAULT_CNF)).expanduser()


class MySQLDatabase:
    """PyMySQL 연결 하나. 첫 ``call``에서 연결한다."""

    def __init__(self, cnf: Path | str | None = None,
                 connect_timeout: int = CONNECT_TIMEOUT_S, read_timeout: int = READ_TIMEOUT_S) -> None:
        self.cnf = Path(cnf).expanduser() if cnf else cnf_path()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._conn: pymysql.connections.Connection | None = None

    def _connect(self) -> pymysql.connections.Connection:
        if self._conn is not None:
            return self._conn
        if not self.cnf.is_file():
            raise DBUnavailable(f"MySQL option file not found: {self.cnf}")
        try:
            self._conn = pymysql.connect(
                read_default_file=str(self.cnf),
                read_default_group="client",
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
        except pymysql.MySQLError as exc:
            raise DBUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return self._conn

    def call(self, proc: str, *args: Any) -> ResultSets:
        """프로시저를 실행하고 결과 집합 전부를 (행 dict 목록의) 목록으로 돌려준다."""
        sql = PROCEDURES[proc]
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                sets: ResultSets = []
                while True:
                    if cur.description is not None:
                        sets.append(list(cur.fetchall()))
                    if not cur.nextset():
                        break
                return sets
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as exc:
            self.close()
            code = exc.args[0] if exc.args else None
            # 2002·2003 연결 불가, 2006·2013 연결 끊김, 1045 접속 거부, 1049 DB 없음
            if code in (1045, 1049, 2002, 2003, 2006, 2013) or isinstance(exc, pymysql.err.InterfaceError):
                raise DBUnavailable(f"{type(exc).__name__}: {exc}") from exc
            raise DBQueryError(f"{type(exc).__name__}: {exc}") from exc
        except pymysql.MySQLError as exc:
            raise DBQueryError(f"{type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except pymysql.MySQLError:
                pass
            self._conn = None


DatabaseFactory = Callable[[], Database]


def default_factory() -> Database:
    return MySQLDatabase(current_app.config.get("KITTI_MYSQL_CNF") or None)


def get_db() -> Database:
    """현재 요청의 DB 연결(없으면 만든다)."""
    if "kitti_db" not in g:
        factory: DatabaseFactory = current_app.config.get("KITTI_DB_FACTORY") or default_factory
        g.kitti_db = factory()
    return g.kitti_db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("kitti_db", None)
    if db is not None:
        db.close()
