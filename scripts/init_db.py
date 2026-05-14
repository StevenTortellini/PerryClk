"""Initialize the database and seed defaults. Idempotent."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config
from app.db import init_db


if __name__ == "__main__":
    path = Config.DATABASE_PATH
    init_db(path)
    print(f"Database initialized at {path}")
