import os

from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from app.catalog import SPEC
from app.config import MAX_FILE, ROOT
from app.errors import DomainIssue, error_response
from app.review.store import Store
from app.routes import api
from app.writeoff.routes import writeoff_api
from app.writeoff.store import WriteOffStore


def create_app(database=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE + 64 * 1024
    db_path = database or os.getenv("WORKBENCH_DB", str(ROOT / "instance" / "workbench.sqlite3"))
    app.extensions["runs"] = Store(db_path)
    app.extensions["writeoff"] = WriteOffStore(db_path)
    app.register_error_handler(DomainIssue, error_response)
    app.register_error_handler(RequestEntityTooLarge, lambda _: error_response(DomainIssue("FILE_TOO_LARGE", 413)))
    app.register_blueprint(api)
    app.register_blueprint(writeoff_api)

    @app.get("/")
    def home():
        wo_summary = None
        try:
            from app.writeoff.service import scenario_board
            result = scenario_board("standard")
            if result:
                board = result["board"]
                wo_summary = {"totals": board["totals"],
                              "orders": [{"order_id": o["order_id"], "counts": o["counts"]}
                                         for o in board["orders"]]}
        except Exception:  # 首页不应因核销模块数据问题而失败
            wo_summary = None
        return render_template("index.html", p=SPEC, wo=wo_summary)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()
