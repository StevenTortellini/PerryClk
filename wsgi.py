"""WSGI entrypoint. Used by Gunicorn (production) and `python wsgi.py` (dev)."""
from app import create_app
from app.config import Config

app = create_app()


if __name__ == "__main__":
    # Dev server only. Production uses gunicorn (see systemd/clk-app.service).
    app.run(host=Config.BIND_HOST, port=Config.BIND_PORT, debug=False)
