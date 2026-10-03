"""per-player Immich accounts and daily challenges

Each player now links their own Immich account (API key, stored encrypted) and only ever plays with
photos that Immich user can see - see services/immich/_scope.py. Since players no longer share a
library, daily challenges become per player too: `daily_challenges.user_id` plus a unique
(day, game_type, mode, user) replacing the old (day, game_type, mode) one. Challenges generated
before this keep user_id NULL - games already played on them stay valid.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "minigames"


def upgrade() -> None:
    op.add_column("users", sa.Column("immich_user_id", postgresql.UUID(), nullable=True), schema=_SCHEMA)
    op.add_column("users", sa.Column("immich_api_key_encrypted", sa.String(), nullable=True), schema=_SCHEMA)
    op.add_column("users", sa.Column("immich_user_name", sa.String(), nullable=True), schema=_SCHEMA)
    op.add_column("users", sa.Column("immich_user_email", sa.String(), nullable=True), schema=_SCHEMA)

    op.add_column(
        "daily_challenges",
        sa.Column(
            "user_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema=_SCHEMA,
    )
    op.drop_constraint("uq_daily_challenges_date_type_mode", "daily_challenges", schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_daily_challenges_date_type_mode_user",
        "daily_challenges",
        ["challenge_date", "game_type", "mode", "user_id"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    # Per-player challenges can't fold back into one shared row per (day, game_type, mode) - drop
    # them (and the games played on them) rather than guess which one survives.
    op.execute(
        f"DELETE FROM {_SCHEMA}.rounds WHERE game_id IN (SELECT g.id FROM {_SCHEMA}.games g "
        f"JOIN {_SCHEMA}.daily_challenges c ON c.id = g.daily_challenge_id WHERE c.user_id IS NOT NULL)"
    )
    op.execute(
        f"DELETE FROM {_SCHEMA}.games WHERE daily_challenge_id IN "
        f"(SELECT id FROM {_SCHEMA}.daily_challenges WHERE user_id IS NOT NULL)"
    )
    op.execute(f"DELETE FROM {_SCHEMA}.daily_challenges WHERE user_id IS NOT NULL")
    op.drop_constraint("uq_daily_challenges_date_type_mode_user", "daily_challenges", schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_daily_challenges_date_type_mode",
        "daily_challenges",
        ["challenge_date", "game_type", "mode"],
        schema=_SCHEMA,
    )
    op.drop_column("daily_challenges", "user_id", schema=_SCHEMA)
    op.drop_column("users", "immich_user_email", schema=_SCHEMA)
    op.drop_column("users", "immich_user_name", schema=_SCHEMA)
    op.drop_column("users", "immich_api_key_encrypted", schema=_SCHEMA)
    op.drop_column("users", "immich_user_id", schema=_SCHEMA)
