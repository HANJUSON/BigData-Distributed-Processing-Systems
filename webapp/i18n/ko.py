"""화면 문구표 (README 9.10).

README 9.10과 DB 과목 README는 경로를 ``app/i18n/ko.py``로 적었지만, 이 저장소의
Flask 패키지 이름은 ``webapp``이므로 실제 경로는 ``webapp/i18n/ko.py``다.

규칙
- 내부 이름(``valid_3d``, ``diff_level`` 등)은 화면에 그대로 내보내지 않는다.
  화면에 보이는 모든 표시 이름은 이 파일 한 곳에서 관리한다.
- 존댓말, 짧은 문장. 버튼은 동작으로 쓴다(‘저장’ ✕ → ‘프리셋 저장’ ○).
- 같은 개념은 같은 단어: 장면(frame) · 객체(object) · 관측 부족 · 가림 · 잘림 · 포인트 수.
- 오류 문구는 ‘무엇이 → 왜 → 어떻게’ 순서, 사용자를 탓하지 않는다.
"""

from __future__ import annotations

APP_NAME = "KITTI Scene Explorer"
LICENSE_LINE = "KITTI Vision Benchmark Suite · CC BY-NC-SA 3.0"

# ---------------------------------------------------------------------------
# 객체 클래스 — KITTI 원본 값 → (표시 이름, CSS 토큰 접미사)
# 순서는 화면의 체크박스·범례 순서다.
# ---------------------------------------------------------------------------
CLASSES: dict[str, dict[str, str]] = {
    "Car": {"label": "차량", "slug": "car"},
    "Van": {"label": "승합차", "slug": "van"},
    "Truck": {"label": "트럭", "slug": "truck"},
    "Pedestrian": {"label": "보행자", "slug": "pedestrian"},
    "Person_sitting": {"label": "앉은 사람", "slug": "person-sitting"},
    "Cyclist": {"label": "자전거", "slug": "cyclist"},
    "Tram": {"label": "트램", "slug": "tram"},
    "Misc": {"label": "기타", "slug": "misc"},
    "DontCare": {"label": "무시 영역", "slug": "dontcare"},
}
CLASS_LABELS: dict[str, str] = {k: v["label"] for k, v in CLASSES.items()}
CLASS_SLUGS: dict[str, str] = {k: v["slug"] for k, v in CLASSES.items()}
#: 검색 조건에 나오는 클래스(무시 영역 제외)
SEARCHABLE_CLASSES: list[str] = [k for k in CLASSES if k != "DontCare"]

# ---------------------------------------------------------------------------
# 가림(occluded) 0–3
# ---------------------------------------------------------------------------
OCCLUSION_LABELS: dict[int, str] = {
    0: "보임",
    1: "일부 가림",
    2: "대부분 가림",
    3: "알 수 없음",
}
#: 표처럼 좁은 곳에 쓰는 짧은 이름
OCCLUSION_SHORT: dict[int, str] = {0: "보임", 1: "일부", 2: "대부분", 3: "모름"}

# ---------------------------------------------------------------------------
# 난이도 (KITTI 공식 적격 기준, DB gt_object.diff_level 1–3, NULL = 등급 외)
# ---------------------------------------------------------------------------
DIFFICULTY_LABELS: dict[int | None, str] = {
    1: "쉬움",
    2: "보통",
    3: "어려움",
    None: "등급 외",
}

# ---------------------------------------------------------------------------
# 검수 (DB 과목) — 상태와 판정 값
# ---------------------------------------------------------------------------
REVIEW_STATUS_LABELS: dict[str, str] = {
    "pending": "미검수",
    "reviewed": "검수 완료",
    "disputed": "이견 있음",
}
VERDICT_LABELS: dict[str, str] = {
    "ok": "정상",
    "label_error": "라벨 오류",
    "low_obs": "관측 부족",
    "other": "기타",
}
SET_KIND_LABELS: dict[str, str] = {"search": "검색", "sample": "표본"}
SET_STATUS_LABELS: dict[str, str] = {"open": "진행 중", "closed": "마감"}

# ---------------------------------------------------------------------------
# 필드 표시 이름 — 내부 열 이름 → 화면 이름
# ---------------------------------------------------------------------------
FIELD_LABELS: dict[str, str] = {
    "frame_id": "장면",
    "object_id": "객체 번호",
    "obj_type": "종류",
    "cls": "종류",
    "dist_m": "거리",
    "occluded": "가림",
    "truncated": "잘림",
    "num_points": "포인트 수",
    "diff_level": "난이도",
    "valid_3d": "3D 박스 유효",
    "is_dontcare": "무시 영역",
    "low_obs": "관측 부족",
    "review_status": "검수",
    "verdict": "판정",
    "bbox_height_px": "2D 박스 높이",
    "match_objects": "일치 객체",
    "nearest_m": "최근접 거리",
    "tags": "태그",
}

# ---------------------------------------------------------------------------
# 정렬 (S1)
# ---------------------------------------------------------------------------
SORT_OPTIONS: dict[str, str] = {
    "match": "일치 객체 많은 순",
    "far": "거리 먼 순",
    "few_points": "포인트 적은 순",
    "frame": "장면 번호 순",
}
DEFAULT_SORT = "match"

#: 빈 결과 제안 — sp_search_summary의 cond 값 → 조건 이름 (“거리 조건을 해제하면 312개”)
RELAX_FIELDS: dict[str, str] = {
    "class": "객체 종류",
    "dist": "거리",
    "occ": "가림",
    "points": "포인트 수",
    "low_only": "관측 부족만",
    "unreviewed": "미검수만",
}

# ---------------------------------------------------------------------------
# 주 메뉴 (공통 헤더) — endpoint, 표시 이름, 소속
# ---------------------------------------------------------------------------
NAV_ITEMS: list[dict[str, str]] = [
    {"endpoint": "pages.search", "label": "장면 검색", "section": "search"},
    {"endpoint": "pages.review_sets", "label": "검수", "section": "review"},
    {"endpoint": "pages.dashboard", "label": "분석", "section": "dashboard"},
    {"endpoint": "pages.pipeline", "label": "처리 결과", "section": "pipeline"},
    {"endpoint": "pages.about", "label": "정보", "section": "about"},
]

# ---------------------------------------------------------------------------
# 상태 문구 (README 9.5)
# ---------------------------------------------------------------------------
STATE_MESSAGES: dict[str, str] = {
    # 로딩
    "loading": "불러오는 중…",
    "loading_results": "검색 결과를 불러오는 중…",
    "loading_points": "점군 불러오는 중…",
    # 빈 결과
    "empty_title": "조건에 맞는 장면이 없습니다",
    "empty_hint": "조건을 하나 풀거나 추천 프리셋으로 시작해 보세요.",
    "empty_relax": "{field} 조건을 해제하면 {count}개가 있습니다.",
    # 오류
    "error_title": "결과를 불러오지 못했습니다",
    "error_db": "데이터베이스에 연결하지 못했습니다. MySQL 서버가 실행 중인지 확인해 주세요.",
    "error_generic": "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "error_details": "자세히",
    "retry": "다시 시도",
    # 부분 성공
    "partial_lidar": "이 장면의 LiDAR 파일을 찾지 못했습니다({filename}).",
    "partial_image": "이 장면의 카메라 이미지를 찾지 못했습니다({filename}).",
    "partial_calib": "이 장면의 보정 파일을 찾지 못해 BEV에 3D 박스를 그리지 못했습니다({filename}).",
    "scene_not_found_title": "장면을 찾을 수 없습니다",
    "scene_not_found_body": "장면 번호는 000000–007480입니다. 장면 검색에서 다시 찾아 주세요.",
    "db_error_title": "데이터를 불러오지 못했습니다",
    "thresholds_error": "기준값을 불러오지 못했습니다. 데이터베이스 연결을 확인해 주세요.",
    "results_missing": "결과 파일이 아직 없습니다",
    "results_missing_body": "배치를 실행해 {filename}을(를) 만들면 이 자리에 표시됩니다.",
    "bench_pending_title": "측정 전입니다",
    "bench_pending_body": "성능 측정(F-12~F-14)을 실행하면 results/bench/summary.csv에서 값을 읽어 표시합니다. 측정 전에는 숫자를 표시하지 않습니다.",
    # 입력 검증
    "invalid_range": "최솟값이 최댓값보다 큽니다.",
    # 알림
    "saved": "저장했습니다.",
    "deleted": "삭제했습니다.",
    "undo": "되돌리기",
    "link_copied": "링크를 복사했습니다.",
    # 404 / 500
    "not_found_title": "페이지를 찾을 수 없습니다",
    "not_found_body": "주소가 바뀌었거나 없는 장면 번호일 수 있습니다. 장면 검색에서 다시 찾아 주세요.",
    "server_error_title": "화면을 표시하지 못했습니다",
    "server_error_body": "서버에서 오류가 났습니다. 잠시 후 다시 시도해 주세요.",
}

#: DB 과목 NR-4 오류 안내 문구 (SIGNAL·제약 위반 → 화면 문구)
DB_ERROR_MESSAGES: dict[str, str] = {
    "duplicate_review": "이 객체에는 이미 판정이 있습니다. 기존 판정을 수정해 주세요.",
    "set_closed": "마감된 세트라 판정을 저장할 수 없습니다. 세트를 다시 열거나 다른 세트에서 검수해 주세요.",
    "empty_set": "조건에 맞는 객체가 없어 세트를 만들 수 없습니다. 검색 조건을 넓혀 주세요.",
    "duplicate_tag": "‘{name}’ 태그가 이미 있습니다. 다른 이름을 입력해 주세요.",
    "fk_restrict": "판정 기록이 있어 삭제할 수 없습니다. 판정 이력을 보존하기 위한 규칙입니다.",
    "check_occluded": "가림 정도는 0–3 사이여야 합니다.",
    "db_unavailable": STATE_MESSAGES["error_db"],
    "query_failed": "데이터베이스에서 결과를 조회하지 못했습니다. 웹 화면용 프로시저(db/03_views_procs.sql)가 적재되었는지 확인해 주세요.",
}

#: S5 MySQL 적재 대조 항목 묶음 (results/mysql_consistency.json의 section)
MYSQL_CHECK_SECTIONS: dict[str, str] = {
    "row_count": "테이블 행 수",
    "null_count": "열별 NULL 수",
    "threshold": "클래스별 관측 부족 기준값",
    "threshold_n": "기준값 계산 대상 객체 수",
    "contradiction": "모순 0건 (유효·NULL·생성 열 대조)",
}

#: S5 성능 실행 방식 (kitti.bench 모드 이름 → 화면 이름)
BENCH_MODE_LABELS: dict[str, str] = {
    "seq": "순차",
    "local1": "local[1]",
    "local4": "local[4]",
    "local8": "local[8]",
}

# ---------------------------------------------------------------------------
# 기타 공통 문구
# ---------------------------------------------------------------------------
UI: dict[str, str] = {
    "skip_link": "본문으로 건너뛰기",
    "reviewer": "검수자",
    "reviewer_placeholder": "검수자 선택",
    "reviewer_disabled": "검수자 선택은 데이터베이스 과목 프로젝트에서 구현합니다. 지금은 검수 기능을 쓸 수 없습니다.",
    "reviewer_required": "헤더에서 검수자를 먼저 선택하세요.",
    "theme_to_dark": "다크 테마로 전환",
    "theme_to_light": "라이트 테마로 전환",
    "shortcuts": "단축키",
    "shortcuts_open": "단축키 도움말 열기",
    "close": "닫기",
    "menu": "메뉴",
    "demo_badge": "데모 데이터",
    "demo_notice": "이 화면의 숫자와 장면은 레이아웃 검토용 데모 데이터입니다. 실제 분석 결과가 아닙니다.",
    "db_course_notice": "이 기능은 데이터베이스 과목 프로젝트에서 구현합니다.",
    "points_null": "계산 안 함(3D 박스 무효)",
    "mobile_review_unsupported": "모바일 화면에서는 검수 입력을 지원하지 않습니다. 1024 px 이상 화면에서 판정을 남겨 주세요.",
    "view_as_table": "표로 보기",
    "view_as_chart": "그래프로 보기",
    "clear_all": "조건 모두 지우기",
    "remove_condition": "{label} 조건 해제",
    "filters": "검색 조건",
    "open_filters": "검색 조건 열기",
    "prev": "이전",
    "next": "다음",
    "back_to_results": "검색 결과",
    "copy_link": "링크 복사",
    "classes_none": "아무것도 고르지 않으면 모든 종류를 찾습니다.",
    "dist_ends": "0 m와 80 m 끝은 ‘제한 없음’입니다(80 m보다 먼 객체 포함).",
    "presets_db_course": "프리셋은 데이터베이스 과목 프로젝트에서 구현합니다.",
    "tags_db_course": "태그 검색은 데이터베이스 과목 프로젝트에서 구현합니다.",
    "unreviewed_note": "검수 기록은 데이터베이스 과목 프로젝트에서 연결합니다. 지금은 모든 객체가 미검수입니다.",
}

#: 단축키 도움말 (README 9.8, DB 판정 패널)
SHORTCUTS: list[tuple[str, str]] = [
    ("/", "검색 조건으로 이동"),
    ("←  →", "이전 · 다음 장면"),
    ("Esc", "돌아가기 · 대화상자 닫기"),
    ("?", "단축키 도움말"),
    ("1 – 4", "판정 고르기 (검수 패널, DB 과목)"),
    ("Enter", "저장하고 다음 (검수 패널, DB 과목)"),
    ("N", "건너뛰기 (검수 패널, DB 과목)"),
]


def class_label(cls: str) -> str:
    """KITTI 클래스 값을 표시 이름으로. 모르는 값은 ‘기타’."""
    return CLASS_LABELS.get(cls, CLASS_LABELS["Misc"])


def class_slug(cls: str) -> str:
    return CLASSES.get(cls, CLASSES["Misc"])["slug"]


def occlusion_label(level: int | None, short: bool = False) -> str:
    table = OCCLUSION_SHORT if short else OCCLUSION_LABELS
    if level is None:
        return table[3]
    return table.get(int(level), table[3])


def difficulty_label(level: int | None) -> str:
    return DIFFICULTY_LABELS.get(level, DIFFICULTY_LABELS[None])
