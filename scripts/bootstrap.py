"""一键生成 .env 所需的密钥。

用法：
  python scripts/bootstrap.py [admin_password] [postgres_password]
  python scripts/bootstrap.py admin1234 secret_pg_pass

默认密码：admin1234（务必通过 UI「修改密码」改掉）
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from passlib.context import CryptContext

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

DEFAULT_PASSWORD = "admin1234"
DEFAULT_PG_PASSWORD = "fwr-CHANGE-ME"


def main() -> None:
    if ENV_PATH.exists():
        print(f".env already exists at {ENV_PATH}, will not overwrite.")
        print("如需重置，先备份再删 .env：mv .env .env.old && python scripts/bootstrap.py")
        return
    if not ENV_EXAMPLE_PATH.exists():
        print(".env.example missing!", file=sys.stderr)
        sys.exit(1)

    password = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PASSWORD
    pg_password = sys.argv[2] if len(sys.argv) > 2 else secrets.token_urlsafe(24)

    if len(password) < 8:
        print(f"❌ 管理密码至少 8 位（你给的是 {len(password)} 位）", file=sys.stderr)
        sys.exit(1)

    template = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    fernet_key = Fernet.generate_key().decode()
    admin_token = secrets.token_urlsafe(32)
    session_secret = secrets.token_urlsafe(48)
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_ctx.hash(password)

    # 替换 .env.example 里的占位
    replacements = [
        ("ADMIN_TOKEN=", f"ADMIN_TOKEN={admin_token}"),
        ("UPSTREAM_KEY_FERNET_KEY=", f"UPSTREAM_KEY_FERNET_KEY={fernet_key}"),
        ("ADMIN_PASSWORD_HASH=", f"ADMIN_PASSWORD_HASH={password_hash}"),
        ("SESSION_TOKEN_SECRET=", f"SESSION_TOKEN_SECRET={session_secret}"),
        ("POSTGRES_PASSWORD=", f"POSTGRES_PASSWORD={pg_password}"),
    ]
    out = template
    for old, new in replacements:
        # 只替换"等号后无内容"的行（避免重复替换/破坏注释）
        out = out.replace(old + "\n", new + "\n", 1)

    ENV_PATH.write_text(out, encoding="utf-8")

    print()
    print("✅ .env 生成完成")
    print()
    print(f"  管理登录密码         : {password}   {'⚠️  默认密码，登录后请改掉' if password == DEFAULT_PASSWORD else ''}")
    print(f"  PostgreSQL 密码      : {pg_password[:8]}...（自动生成，仅用于内部 docker network）")
    print(f"  ADMIN_TOKEN          : {admin_token[:20]}...（CLI/CI backdoor）")
    print(f"  UPSTREAM_KEY_FERNET  : {fernet_key[:20]}...  ⚠️ 务必备份！")
    print(f"  SESSION_TOKEN_SECRET : {session_secret[:20]}...")
    print()
    print("下一步：")
    print("  docker compose up -d --build")
    print()
    print("如需 HTTPS：先编辑 .env 加 DOMAIN=your.example.com，再 docker compose --profile https up -d")


if __name__ == "__main__":
    main()
