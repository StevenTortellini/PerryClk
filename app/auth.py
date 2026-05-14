"""User authentication via Flask-Login + Argon2 password hashing."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask_login import LoginManager, UserMixin

from .db import get_db


login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"

_hasher = PasswordHasher()


class User(UserMixin):
    def __init__(self, id_: int, username: str):
        self.id = id_
        self.username = username

    @staticmethod
    def from_row(row) -> "User":
        return User(id_=row["id"], username=row["username"])


@login_manager.user_loader
def load_user(user_id: str):
    row = get_db().execute(
        "SELECT id, username FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return User.from_row(row) if row else None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def authenticate(username: str, password: str) -> User | None:
    row = get_db().execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return User.from_row(row)
