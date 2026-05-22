"""一键生成 .env 所需的密钥；不会覆盖已有 .env。"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from passlib.context import CryptContext

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

DEFAULT_PASSWORD = "admin"


def main() -> None:
    if ENV_PATH.exists():
        print(f".env already exists at {ENV_PATH}, will not overwrite.")
        print("Edit it manually if you need to rotate keys.")
        return
    if not ENV_EXAMPLE_PATH.exists():
        print(".env.example missing!", file=sys.stderr)
        sys.exit(1)

    password = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PASSWORD

    template = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    fernet_key = Fernet.generate_key().decode()
    admin_token = secrets.token_urlsafe(32)
    session_secret = secrets.token_urlsafe(48)
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_ctx.hash(password)

    out = template
    out = out.replace(
        "ADMIN_TOKEN=please-change-this-to-a-random-string",
        f"ADMIN_TOKEN={admin_token}",
    )
    out = out.replace(
        "UPSTREAM_KEY_FERNET_KEY=",
        f"UPSTREAM_KEY_FERNET_KEY={fernet_key}",
    )
    out = out.replace(
        "ADMIN_PASSWORD_HASH=",
        f"ADMIN_PASSWORD_HASH={password_hash}",
    )
    out = out.replace(
        "SESSION_TOKEN_SECRET=please-change-me-to-a-random-string-of-32-chars-or-more",
        f"SESSION_TOKEN_SECRET={session_secret}",
    )
    ENV_PATH.write_text(out, encoding="utf-8")

    print("Wrote .env with newly generated:")
    print(f"  ADMIN_PASSWORD (登录密码)    = {password}   {'⚠️  默认密码，建议改' if password == DEFAULT_PASSWORD else ''}")
    print(f"  ADMIN_PASSWORD_HASH           = {password_hash[:30]}... (bcrypt)")
    print(f"  ADMIN_TOKEN (CLI backdoor)    = {admin_token}")
    print(f"  UPSTREAM_KEY_FERNET_KEY       = {fernet_key}")
    print(f"  SESSION_TOKEN_SECRET          = {session_secret[:20]}...")
    print()
    print("用法：python scripts/bootstrap.py [your_password]")
    print("登录后 UI 用第一个 ADMIN_PASSWORD；CLI 脚本可继续用 ADMIN_TOKEN backdoor。")


if __name__ == "__main__":
    main()
