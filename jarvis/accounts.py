"""여러 사람이 쓰기(2026-10-11): 비밀번호 해시와 로그인 토큰. 표준 라이브러리만 쓴다.

비밀번호는 scrypt 로만 저장하고, 로그인 쿠키에 담는 토큰은 DB 에 SHA-256 해시로만 남긴다 —
DB 파일이 새어도 비밀번호나 살아 있는 로그인을 바로 쓸 수 없다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD = 8
LOGIN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
ROLES = ("admin", "member")
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다.")
    if len(password) > 200:
        raise ValueError("비밀번호가 너무 깁니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    encode = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8")[:1000], salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def clean_login(login: object) -> str:
    value = str(login or "").strip().lower()
    if not LOGIN_RE.fullmatch(value):
        raise ValueError("아이디는 영문 소문자·숫자로 2~32자입니다(. _ - 사용 가능).")
    return value


def clean_name(name: object) -> str:
    value = " ".join(str(name or "").split())
    if not value or len(value) > 40:
        raise ValueError("이름은 1~40자로 적어 주세요.")
    return value


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
