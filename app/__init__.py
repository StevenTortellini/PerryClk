"""Flask application factory."""
from __future__ import annotations

import logging

import structlog
from flask import Flask
from flask_wtf.csrf import CSRFProtect

from .auth import login_manager
from .config import Config
from .db import close_db, get_setting, init_db, standalone_connection
from .worker import init_worker


csrf = CSRFProtect()


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def create_app(config_class: type = Config) -> Flask:
    _configure_logging()

    app = Flask(__name__)
    app.config.from_object(config_class)

    # Database
    init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(close_db)

    # Extensions
    login_manager.init_app(app)
    csrf.init_app(app)

    # CSRF on the JSON API would interfere with HTMX; we exempt /api and /webhook
    # routes individually below. (Auth on /api is still required via @login_required.)
    from .blueprints.api import bp as api_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.ui import bp as ui_bp
    from .blueprints.webhook import bp as webhook_bp

    csrf.exempt(api_bp)
    csrf.exempt(webhook_bp)

    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(webhook_bp)

    # Background worker — use saved settings if available, else env defaults
    with standalone_connection(app.config["DATABASE_PATH"]) as conn:
        port = get_setting(conn, "rs485_port") or app.config["RS485_PORT"]
        baud = int(get_setting(conn, "rs485_baud") or app.config["RS485_BAUD"])
        address = int(get_setting(conn, "clock_address") or app.config["CLOCK_ADDRESS"])

    init_worker(
        db_path=app.config["DATABASE_PATH"],
        port=port,
        baud=baud,
        clock_address=address,
    )

    return app
