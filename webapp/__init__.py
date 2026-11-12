"""KITTI Scene Explorer — Flask 앱 (README 7절·9절).

실행::

    flask --app webapp run --debug          # 개발 (/_components 갤러리 사용 가능)
    flask --app webapp run --port 5050      # 시연

데이터 출처 (기본 = 실제 데이터)
- S1·S2·S6: MySQL 서비스 DB 프로시저(``webapp.db``). 접속 정보는 ``~/.my.cnf``의 ``[client]``
  (경로는 환경변수 ``KITTI_MYSQL_CNF``로 변경). 저장소에 비밀번호를 두지 않는다.
- S2 이미지·점군·보정: ``KITTI_ROOT``/training (``kitti.config``)
- S1 썸네일: ``KITTI_WORK``/thumbs (환경변수 ``KITTI_THUMBS``로 변경)
- S5: 저장소의 ``results/`` (환경변수 ``KITTI_RESULTS``로 변경)

``KITTI_DEMO=1``이면 S1·S2·S6이 ``webapp.demo_data``의 데모 값을 쓴다(레이아웃 검토·시험 전용).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, render_template

from kitti import config as kcfg

from .db import close_db
from .filters import register_filters
from .i18n import ko

PROJECT_DIR = Path(__file__).resolve().parents[1]

#: 모든 응답에 붙이는 보안 헤더. 템플릿에 인라인 <script>·이벤트 처리기 속성이 없으므로 script-src는 'self'만.
#: style 속성(진행 막대 폭, 범위 슬라이더 --lo/--hi 등)과 <noscript><style>이 있어 style-src는 'unsafe-inline'을 둔다.
#: type="application/json" 데이터 블록은 실행되지 않으므로 CSP 대상이 아니다.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_mapping(
        JSON_AS_ASCII=False,
        # 정적 파일 캐시 무효화용 버전. 배포 시 올린다.
        ASSET_VERSION="0.2.0",
        # True면 debug가 아니어도 /_components 갤러리를 연다(시각 QA용).
        COMPONENT_GALLERY=False,
        # 데모 데이터(시험·레이아웃 검토 전용). 기본은 실제 데이터.
        KITTI_DEMO=os.environ.get("KITTI_DEMO") == "1",
        # MySQL 옵션 파일. None이면 KITTI_MYSQL_CNF 또는 ~/.my.cnf
        KITTI_MYSQL_CNF=None,
        # 시험에서 가짜 DB를 넣는 자리: 인자 없는 callable → webapp.db.Database
        KITTI_DB_FACTORY=None,
        KITTI_TRAINING_DIR=str(kcfg.training_dir()),
        KITTI_THUMBS_DIR=os.environ.get("KITTI_THUMBS", str(kcfg.KITTI_WORK / "thumbs")),
        KITTI_RESULTS_DIR=os.environ.get("KITTI_RESULTS", str(PROJECT_DIR / "results")),
    )
    if test_config:
        app.config.update(test_config)

    register_filters(app)
    app.teardown_appcontext(close_db)
    # 매크로는 컨텍스트 없이 import되므로 문구표를 전역으로 둔다.
    app.jinja_env.globals["ko"] = ko

    from .blueprints.dev import bp as dev_bp
    from .blueprints.pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(dev_bp)

    @app.after_request
    def security_headers(resp):  # noqa: ANN001, ANN202
        for name, value in SECURITY_HEADERS.items():
            resp.headers.setdefault(name, value)
        return resp

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"ko": ko, "asset_v": app.config["ASSET_VERSION"]}

    @app.errorhandler(404)
    def not_found(_err):  # noqa: ANN001
        return render_template("pages/error.html", code=404,
                               title=ko.STATE_MESSAGES["not_found_title"],
                               body=ko.STATE_MESSAGES["not_found_body"]), 404

    @app.errorhandler(500)
    def server_error(_err):  # noqa: ANN001
        return render_template("pages/error.html", code=500,
                               title=ko.STATE_MESSAGES["server_error_title"],
                               body=ko.STATE_MESSAGES["server_error_body"]), 500

    return app
