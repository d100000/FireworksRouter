"""一键生成 .env 所需的密钥。

用法：
  python scripts/bootstrap.py [admin_password] [postgres_password]
  python scripts/bootstrap.py admin1234 secret_pg_pass

默认密码：admin1234（务必通过 UI「修改密码」改掉）

直接用 bcrypt 库做哈希（不走 passlib），避免 passlib 1.7.4 与 bcrypt 4.1+
的兼容性问题（passlib 读 bcrypt.__about__.__version__ 在新版会失败）。
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

DEFAULT_PASSWORD = "admin1234"
# bcrypt 协议硬上限：密码字节长度 ≤ 72。超出会被静默截断（OpenBSD 实现）
# 或在新版库里直接 ValueError。我们主动拒绝以避免用户产生混淆。
BCRYPT_MAX_BYTES = 72


def _replace_env_var(text: str, var_name: str, new_value: str) -> tuple[str, bool]:
    """替换 .env 文件中某个变量的值。

    匹配规则：行首（非注释）的 `VAR_NAME=任意内容`（包括空值、含特殊字符的密码），
    用正则 + multiline 模式，兼容 LF / CRLF 行尾。

    返回 (新文本, 是否替换成功)。
    """
    # 转义 new_value 中的反斜杠（re.sub 替换串里 \ 是特殊字符）
    escaped_value = new_value.replace("\\", "\\\\")
    pattern = re.compile(rf"^{re.escape(var_name)}=.*$", re.MULTILINE)
    new_text, count = pattern.subn(f"{var_name}={escaped_value}", text, count=1)
    return new_text, count > 0


def hash_password(password: str) -> str:
    """用 bcrypt 直接哈希；输出与 passlib 的 bcrypt 完全兼容（同一种 $2b$ 格式）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


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

    # 长度校验（字符数 ≥ 8，字节数 ≤ 72）
    if len(password) < 8:
        print(f"❌ 管理密码至少 8 位（你给的是 {len(password)} 位）", file=sys.stderr)
        sys.exit(1)

    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > BCRYPT_MAX_BYTES:
        print(
            f"❌ 管理密码字节长度 {len(pw_bytes)} 超出 bcrypt 上限 {BCRYPT_MAX_BYTES}。\n"
            f"   提示：英文字符 1 字节，中文字符 ≈ 3 字节。请缩短密码后重试。",
            file=sys.stderr,
        )
        sys.exit(1)

    template = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

    fernet_key = Fernet.generate_key().decode()
    admin_token = secrets.token_urlsafe(32)
    session_secret = secrets.token_urlsafe(48)
    password_hash = hash_password(password)

    # 5 个必填字段：用正则替换，兼容 .env.example 里"已有值"和"空值"两种情况
    replacements = [
        ("ADMIN_TOKEN", admin_token),
        ("UPSTREAM_KEY_FERNET_KEY", fernet_key),
        ("ADMIN_PASSWORD_HASH", password_hash),
        ("SESSION_TOKEN_SECRET", session_secret),
        ("POSTGRES_PASSWORD", pg_password),
    ]
    out = template
    missing: list[str] = []
    for var, val in replacements:
        out, replaced = _replace_env_var(out, var, val)
        if not replaced:
            # .env.example 缺这个字段；追加到文末
            out = out.rstrip("\r\n") + f"\n{var}={val}\n"
            missing.append(var)

    if missing:
        print(f"⚠️  .env.example 中缺以下字段，已追加：{', '.join(missing)}", file=sys.stderr)

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
