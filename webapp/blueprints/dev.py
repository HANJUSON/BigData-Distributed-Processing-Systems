"""개발용 화면: 컴포넌트 갤러리 ``/_components`` (debug 모드에서만)."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template

from .. import demo_data

bp = Blueprint("dev", __name__)


@bp.get("/_components")
def components():
    if not (current_app.debug or current_app.config.get("COMPONENT_GALLERY")):
        abort(404)
    sample = demo_data.search_results()["cards"][:2]
    scene = demo_data.get_scene(123)
    return render_template("pages/components.html", section=None,
                           sample_cards=sample, scene=scene)
