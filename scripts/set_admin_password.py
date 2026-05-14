"""Create or update the admin user. Run this once after init_db.py."""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password
from app.config import Config
from app.db import standalone_connection


def main() -> None:
    username = input("Username [admin]: ").strip() or "admin"
    while True:
        pw = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm:  ")
        if pw == pw2 and len(pw) >= 8:
            break
        print("Passwords don't match or are shorter than 8 characters.")

    pw_hash = hash_password(pw)
    with standalone_connection(Config.DATABASE_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (username, password_hash) VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
            """,
            (username, pw_hash),
        )
        conn.commit()
    print(f"User '{username}' is ready.")


if __name__ == "__main__":
    main()
