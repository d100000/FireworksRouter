"""CLI：直接在 DB 里创建一个 user_token（绕过 admin HTTP，方便首次自测）。"""

from __future__ import annotations

import asyncio

import click

from app.db import init_db, session_scope
from app.models import UserToken, UserTokenStatus
from app.utils.tokens import generate_user_token, hash_token


async def _create(owner: str, name: str, unlimited: bool) -> str:
    await init_db()
    token = generate_user_token()
    async with session_scope() as session:
        record = UserToken(
            owner=owner,
            name=name,
            token_hash=hash_token(token),
            token_preview=f"{token[:10]}...{token[-4:]}",
            status=UserTokenStatus.active,
            unlimited_quota=unlimited,
            stream_enabled=True,
        )
        session.add(record)
    return token


@click.command()
@click.option("--owner", default="cli", help="所属用户标识")
@click.option("--name", default="cli-token", help="名称")
@click.option("--limited/--unlimited", default=False, help="是否限额（默认无限）")
def main(owner: str, name: str, limited: bool) -> None:
    token = asyncio.run(_create(owner, name, unlimited=not limited))
    click.echo("Created user token (save it — will not be shown again):")
    click.echo(token)


if __name__ == "__main__":
    main()
