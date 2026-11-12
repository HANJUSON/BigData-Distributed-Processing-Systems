"""화면 라우트 S1–S6 (README 9.3).

| ID | 경로 | 소속 | 데이터 |
| S1 | ``/`` | 공동 | MySQL ``sp_search_scenes_ex`` (+ ``sp_search_summary``·``sp_scene_cards``) |
| S2 | ``/scenes/<frame_id>`` | 공동 | MySQL ``sp_scene_detail`` + 원본 이미지·``.bin``·calib |
| S3 | ``/review-sets`` | DB 과목 (여기서는 안내 화면) | — |
| S4 | ``/dashboard`` | DB 과목 (여기서는 안내 화면) | — |
| S5 | ``/pipeline`` | 빅데이터 과목 | 결과 파일(JSON·CSV·PNG) |
| S6 | ``/about`` | 공동 | 정적 문서 + MySQL ``sp_thresholds`` |

``KITTI_DEMO``가 켜져 있으면(시험·레이아웃 검토 전용) S1·S2·S6은 ``demo_data``를 쓴다. 기본은 실제 데이터다.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from flask import (Blueprint, Response, abort, current_app, render_template, request,
                   send_file, send_from_directory, url_for)

from .. import demo_data, results as s5, service
from ..db import DBError, get_db
from ..i18n import ko

bp = Blueprint("pages", __name__)

DIST_MIN, DIST_MAX, DIST_STEP = 0, 80, 5
DIST_LIMIT = 200          # 거리 입력 상한 (m). 슬라이더 끝 80은 ‘제한 없음’
OCC_DEFAULT = service.OCC_DEFAULT_SET  # 가림 기본 집합 0·1·2 (sp_search_scenes 기본 범위와 같음)
THUMB_MAX_AGE = 365 * 24 * 3600
IMAGE_MAX_AGE = 30 * 24 * 3600


def _demo() -> bool:
    return bool(current_app.config.get("KITTI_DEMO"))


def _training() -> Path:
    return Path(current_app.config["KITTI_TRAINING_DIR"])


# ---------------------------------------------------------------------------
# S1 검색 조건 ↔ URL 쿼리 (9.4 ‘URL 동기화’)
# ---------------------------------------------------------------------------
def _int_arg(args, name: str, default: int | None, lo: int, hi: int) -> int | None:  # noqa: ANN001
    raw = args.get(name)
    if raw in (None, ""):
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def parse_search_state(args) -> dict:  # noqa: ANN001
    """쿼리 문자열을 sp_search_scenes_ex 조건으로 해석한다. 필터링은 하지 않는다(NR-1).

    - 객체 종류: 여러 개(``cls``). 없으면 모든 클래스.
    - 거리: ``dmin``–``dmax`` (m). 슬라이더 양 끝(0, 80)은 ‘제한 없음’으로 본다.
    - 가림: ``occ`` 값의 집합. 없으면 기본 0·1·2.
    - 포인트 수: ``pmin``–``pmax``. 관측 부족만·미검수만은 켜기/끄기. 정렬은 README 9.4의 4가지.
    """
    classes = [c for c in ko.SEARCHABLE_CLASSES if c in set(args.getlist("cls"))]
    occ = sorted({int(o) for o in args.getlist("occ") if o in {"0", "1", "2", "3"}}) or list(OCC_DEFAULT)
    dmin = _int_arg(args, "dmin", None, DIST_MIN, DIST_LIMIT)
    dmax = _int_arg(args, "dmax", None, DIST_MIN, DIST_LIMIT)
    sort = args.get("sort", ko.DEFAULT_SORT)
    if sort not in ko.SORT_OPTIONS:
        sort = ko.DEFAULT_SORT
    return {
        "cls": classes,
        "dmin": None if dmin == DIST_MIN else dmin,
        "dmax": None if dmax == DIST_MAX else dmax,
        "occ": occ,
        "pmin": _int_arg(args, "pmin", None, 0, 10**7),
        "pmax": _int_arg(args, "pmax", None, 0, 10**7),
        "low_obs": args.get("low_obs") == "1",
        "unreviewed": args.get("unreviewed") == "1",
        "sort": sort,
        "page": _int_arg(args, "page", 1, 1, 10**6),
    }


def _pairs(args, drop: tuple[str, ...] = ("page",)) -> list[tuple[str, str]]:  # noqa: ANN001
    return [(k, v) for k in args for v in args.getlist(k) if k not in drop]


def _href(path: str, pairs: list[tuple[str, str]]) -> str:
    return path + ("?" + urlencode(pairs) if pairs else "")


def _range_label(name: str, lo: int | None, hi: int | None, unit: str = "") -> str:
    if lo is not None and hi is not None:
        return f"{name} {lo:,}–{hi:,}{unit}"
    if lo is not None:
        return f"{name} ≥ {lo:,}{unit}"
    return f"{name} ≤ {hi:,}{unit}"


def build_chips(state: dict, args) -> list[dict]:  # noqa: ANN001
    """적용 조건 칩 목록. 각 칩은 그 조건만 뺀 URL을 가진다."""
    path = url_for("pages.search")
    chips = []
    for c in state["cls"]:
        pairs = [(k, v) for k, v in _pairs(args) if not (k == "cls" and v == c)]
        chips.append({"label": ko.class_label(c), "slug": ko.class_slug(c), "href": _href(path, pairs)})
    if state["dmin"] is not None or state["dmax"] is not None:
        chips.append({"label": _range_label("거리", state["dmin"], state["dmax"], " m"),
                      "href": _href(path, _pairs(args, ("dmin", "dmax", "page")))})
    if state["occ"] != list(OCC_DEFAULT):
        label = "가림: " + " · ".join(ko.occlusion_label(o) for o in state["occ"])
        chips.append({"label": label, "href": _href(path, _pairs(args, ("occ", "page")))})
    if state["pmin"] is not None or state["pmax"] is not None:
        chips.append({"label": _range_label("포인트", state["pmin"], state["pmax"]),
                      "href": _href(path, _pairs(args, ("pmin", "pmax", "page")))})
    if state["low_obs"]:
        chips.append({"label": "관측 부족만", "href": _href(path, _pairs(args, ("low_obs", "page"))), "warn": True})
    if state["unreviewed"]:
        chips.append({"label": "미검수만", "href": _href(path, _pairs(args, ("unreviewed", "page")))})
    return chips


def scene_href(frame_id: int, index: int | None, args) -> str:  # noqa: ANN001
    """검색 조건과 결과 안 순번(i)을 담은 S2 링크 — 상세에서 위치·이전·다음을 다시 계산한다."""
    pairs = _pairs(args, ("page", "i"))
    if index is not None:
        pairs.append(("i", str(index)))
    return _href(url_for("pages.scene", frame_id=frame_id), pairs)


def _error_context(exc: DBError) -> dict:
    """DB 오류 화면 문구. 기술 정보(exc.detail: 드라이버 메시지·호스트 등)는 debug일 때만 ‘자세히’에 보이고,
    운영에서는 로그에만 남긴다(호출하는 쪽이 logger.warning으로 이미 기록)."""
    return {"title": ko.STATE_MESSAGES["db_error_title"], "body": ko.DB_ERROR_MESSAGES[exc.kind],
            "details": exc.detail if current_app.debug else None}


# ---------------------------------------------------------------------------
# S1
# ---------------------------------------------------------------------------
@bp.get("/")
def search():
    state = parse_search_state(request.args)
    args = request.args
    error = None
    if _demo():
        results = demo_data.search_results()
        for i, card in enumerate(results["cards"], start=1):
            card["href"] = scene_href(card["frame_id"], None, args)
            card["thumb"] = None
            card["index"] = i
    else:
        try:
            results = service.search(get_db(), state)
        except DBError as exc:
            current_app.logger.warning("S1 DB error: %s", exc.detail)
            results, error = None, _error_context(exc)
        else:
            state["page"] = results["page"]
            for card in results["cards"]:
                card["href"] = scene_href(card["frame_id"], card["index"], args)
                card["thumb"] = url_for("pages.thumbnail", frame_id=card["frame_id"])
                card["show_far"] = state["sort"] == "far"
            relax = results.pop("relax")
            results["relax_hint"] = ({"field": ko.RELAX_FIELDS.get(relax["cond"], relax["cond"]),
                                      "count": relax["count"]} if relax else None)

    def page_href(n: int) -> str:
        return _href(url_for("pages.search"), _pairs(args) + [("page", str(n))])

    html = render_template(
        "pages/search.html",
        section="search",
        page_href=page_href,
        state=state,
        chips=build_chips(state, args),
        results=results,
        error=error,
        presets=demo_data.DEMO_PRESETS if _demo() else [],
        dist_bounds=(DIST_MIN, DIST_MAX, DIST_STEP),
    )
    return (html, 503) if error else html


# ---------------------------------------------------------------------------
# S2
# ---------------------------------------------------------------------------
def _scene_links(scene: dict, args) -> None:  # noqa: ANN001
    """위치(index)로 이전·다음·검색 결과로 돌아가기 링크를 만든다."""
    pos = scene.get("position")
    back_pairs = _pairs(args, ("page", "i"))
    if pos:
        page = (pos["index"] - 1) // service.PAGE_SIZE + 1
        if page > 1:
            back_pairs.append(("page", str(page)))
        pos["prev_href"] = None if pos["prev"] is None else scene_href(pos["prev"], pos["index"] - 1, args)
        pos["next_href"] = None if pos["next"] is None else scene_href(pos["next"], pos["index"] + 1, args)
    scene["back_href"] = _href(url_for("pages.search"), back_pairs)


@bp.get("/scenes/<int:frame_id>")
def scene(frame_id: int):
    if not service.valid_frame_id(frame_id):
        abort(404)
    if _demo():
        data = demo_data.get_scene(frame_id)
        pos = data.get("position")
        if pos:
            pos["prev_href"] = None if pos["prev"] is None else url_for("pages.scene", frame_id=pos["prev"])
            pos["next_href"] = None if pos["next"] is None else url_for("pages.scene", frame_id=pos["next"])
        data["back_href"] = url_for("pages.search")
        return render_template("pages/scene.html", section="search", scene=data)
    try:
        db = get_db()
        data = service.scene_detail(db, frame_id, _training())
        if data is None:
            abort(404)
        index = _int_arg(request.args, "i", None, 1, 10**6)
        if index is not None:
            data["position"] = service.position(db, parse_search_state(request.args), index, frame_id)
    except DBError as exc:
        current_app.logger.warning("S2 DB error: %s", exc.detail)
        err = _error_context(exc)
        return render_template("pages/error.html", code=503, title=err["title"], body=err["body"],
                               details=err["details"], retry_href=request.full_path), 503
    _scene_links(data, request.args)
    return render_template("pages/scene.html", section="search", scene=data)


@bp.get("/scenes/<int:frame_id>/points.bin")
def scene_points(frame_id: int):
    """관심 영역 안의 점군: float32 리틀 엔디언 (x, y, z, 반사 강도) 반복 (9.4)."""
    if not service.valid_frame_id(frame_id):
        abort(404)
    if _demo():
        payload = demo_data.demo_points(frame_id)
    else:
        payload = service.roi_points(_training(), frame_id)
        if payload is None:
            abort(404)
    resp = Response(payload, mimetype="application/octet-stream")
    resp.headers["X-Point-Stride"] = "16"
    if _demo():
        resp.headers["X-Demo-Data"] = "1"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.get("/scenes/<int:frame_id>/image.png")
def scene_image(frame_id: int):
    """원본 카메라 이미지 (image_2). frame_id는 정수 0–7480만 받는다."""
    if _demo() or not service.valid_frame_id(frame_id):
        abort(404)
    path = service.frame_file(_training(), "image_2", frame_id)
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png", max_age=IMAGE_MAX_AGE, conditional=True)


@bp.get("/thumbs/<int:frame_id>.webp")
def thumbnail(frame_id: int):
    """결과 카드 썸네일 (배치가 만든 WebP, 9.9). 오래 캐시한다."""
    if _demo() or not service.valid_frame_id(frame_id):
        abort(404)
    path = Path(current_app.config["KITTI_THUMBS_DIR"]) / f"{frame_id:06d}.webp"
    if not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="image/webp", max_age=THUMB_MAX_AGE, conditional=True)
    resp.headers["Cache-Control"] = f"public, max-age={THUMB_MAX_AGE}, immutable"
    return resp


# ---------------------------------------------------------------------------
# S3–S6
# ---------------------------------------------------------------------------
@bp.get("/review-sets")
def review_sets():
    return render_template("pages/review_sets.html", section="review")


@bp.get("/dashboard")
def dashboard():
    return render_template("pages/dashboard.html", section="dashboard")


@bp.get("/pipeline")
def pipeline():
    results = Path(current_app.config["KITTI_RESULTS_DIR"])
    return render_template("pages/pipeline.html", section="pipeline", p=s5.load(results),
                           has_file=lambda rel: s5.servable(results, rel))


@bp.get("/pipeline/files/<path:rel>")
def result_file(rel: str):
    """S5 원본 파일·그림 내려받기. results 폴더 안의 json·csv·md·png만."""
    results = Path(current_app.config["KITTI_RESULTS_DIR"])
    if not s5.servable(results, rel):
        abort(404)
    return send_from_directory(results, rel, max_age=300)


@bp.get("/about")
def about():
    if _demo():
        return render_template("pages/about.html", section="about", thresholds=demo_data.DEMO_THRESHOLDS,
                               threshold_at=demo_data.DEMO_THRESHOLD_COMPUTED_AT, thresholds_demo=True)
    try:
        values, at = service.thresholds(get_db())
        error = None
    except DBError as exc:
        current_app.logger.warning("S6 DB error: %s", exc.detail)
        values, at, error = {}, "—", _error_context(exc)
    return render_template("pages/about.html", section="about", thresholds=values, threshold_at=at,
                           thresholds_demo=False, thresholds_error=error)
