"""Jinja2 표기 필터 (README 9.6 숫자 표기).

값은 바꾸지 않고 표기만 바꾼다(DB NR-1: 화면은 표시 형식만 맡는다).
"""

from __future__ import annotations

from flask import Flask

from .i18n import ko


def fmt_int(value) -> str:  # noqa: ANN001
    """천 단위 쉼표. ``None``은 ‘—’."""
    if value is None:
        return "—"
    return f"{int(value):,}"


def fmt_dist(value) -> str:  # noqa: ANN001
    """거리: 소수 1자리 + ' m'."""
    if value is None:
        return "—"
    return f"{float(value):,.1f} m"


def fmt_pct(value) -> str:  # noqa: ANN001
    """비율(0–1 또는 0–100 중 0–1 기준): 소수 1자리 %."""
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def fmt_frame(value) -> str:  # noqa: ANN001
    """frame_id → 6자리 0 채움 (KITTI 파일명과 같음)."""
    return f"{int(value):06d}"


def fmt_sec(value) -> str:  # noqa: ANN001
    if value is None:
        return "—"
    return f"{float(value):,.1f} s"


def register_filters(app: Flask) -> None:
    app.jinja_env.filters.update(
        int=fmt_int,
        dist=fmt_dist,
        pct=fmt_pct,
        frame=fmt_frame,
        sec=fmt_sec,
        cls_label=ko.class_label,
        cls_slug=ko.class_slug,
        occ_label=ko.occlusion_label,
        diff_label=ko.difficulty_label,
    )
    # 0.00 형식(잘림 값)
    app.jinja_env.filters["fixed2"] = lambda v: "—" if v is None else f"{float(v):.2f}"
