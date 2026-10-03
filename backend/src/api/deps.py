"""Shared FastAPI dependencies used by more than one router (api/api.py, api/auth_api.py,
api/daily_api.py)."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from config import Settings, get_settings
from persistence.base import get_session_factory
from persistence.games_repository import GameRepository
from persistence.users import UserModel
from services import immich_key_vault
from services.auth_service import UnauthorizedError
from services.daily_challenge_service import DailyChallengeService
from services.daily_games_service import DailyGamesService
from services.daily_settings import DailySettingsService
from services.embedding_jobs import EmbeddingJobRunner
from services.errors import ImmichNotLinkedError
from services.game_factory import GameFactory
from services.game_settings_service import GameSettingsService
from services.games_service import GamesService
from services.immich import ImmichService
from services.invite_service import InviteService
from services.ml_service import MLService
from services.notifications import NotificationService
from services.notifications.runner import NotificationRunner
from services.reports_service import ReportsService
from services.scores_service import ScoresService

_session_factory = get_session_factory()


def get_db_session(request: Request) -> Iterator[Session]:
    # api/auth_middleware.py resolves request.state.user via its own session
    # *before* routing even happens, and stashes that same session on request.state.db_session.
    # Reusing it here (rather than opening a second one) isn't just an optimization: state.user is
    # a UserModel loaded on that session, and a route that mutates it (e.g. change_password) needs
    # it attached to the *same* session it calls session.commit() on, or the mutation is silently
    # lost on a detached object nothing ever flushes. The middleware owns closing this one (after
    # the whole request finishes, in its own finally) - only open+close a fresh session here for
    # the few allow-listed routes the middleware never touches at all (login/register/etc).
    existing = getattr(request.state, "db_session", None)
    if existing is not None:
        yield existing
        return

    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request) -> UserModel:
    """Resolves the current user from request.state, where api/auth_middleware.py already placed
    it for every request that reaches here (anything outside its allow-list). Routes declare
    Depends(get_current_user) as their auth dependency, decoupled from where the identity itself
    comes from. The defensive None-check below should never actually trigger (the middleware
    guarantees state.user is set for anything that isn't allow-listed, and no allow-listed route
    uses this dependency), but costs nothing to keep. Lives here (re-exported by api/auth_api.py)
    so get_immich_service below can depend on it without importing auth_api.py, which imports
    this module."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise UnauthorizedError("not authenticated")
    return user


def immich_service_for(user: UserModel) -> ImmichService:
    """An ImmichService scoped to `user`'s own linked Immich account - every query only sees what
    that Immich user can see, and images are fetched with their own key (see services/immich/).
    Raises ImmichNotLinkedError if they haven't linked one (or it can't be decrypted anymore).
    Shared by get_immich_service below and the places that act on behalf of a user other than the
    caller (an admin setting someone's skin, the notification runner)."""
    api_key = immich_key_vault.decrypt(user.immich_api_key_encrypted)
    if user.immich_user_id is None or api_key is None:
        raise ImmichNotLinkedError()
    return ImmichService(user_id=user.immich_user_id, api_key=api_key)


# Here (not api/api.py) so api/auth_api.py can also depend on ImmichService (to validate a
# skin's person_id, see PUT /auth/me/skin) without a circular import - api.py already imports
# auth_api.py's router, so the reverse import would loop.
def get_immich_service(user: Annotated[UserModel, Depends(get_current_user)]) -> ImmichService:
    return immich_service_for(user)


def get_admin_immich_service(
    user: Annotated[UserModel, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImmichService:
    """Unscoped (installation-wide) - only for admin-only routes, which already gate on
    get_current_admin_user themselves (e.g. resolving names of entities other players reported).
    Images go out with the admin's own linked key, falling back to the optional installation-wide
    IMMICH_API_KEY when they haven't linked one."""
    api_key = immich_key_vault.decrypt(user.immich_api_key_encrypted) or settings.immich_api_key
    return ImmichService(user_id=None, api_key=api_key)


@lru_cache(maxsize=1)
def get_ml_service() -> MLService:
    return MLService()


# Here (not private to a future api/admin_workers_api.py) so main.py's lifespan can also depend on
# it (to cancel + join on shutdown) without importing an api/*_api.py router module for it. Reuses
# get_ml_service()'s single memoized instance (see EmbeddingJobRunner's own docstring for why a
# second, unmemoized MLService() here would mean a second pair of connection pools).
@lru_cache(maxsize=1)
def get_embedding_job_runner() -> EmbeddingJobRunner:
    return EmbeddingJobRunner(get_ml_service())


# Here (not api/admin_invites_api.py) so api/admin_api.py can also depend on it (the
# password-reset endpoint) without admin_api.py <-> admin_invites_api.py becoming a circular
# import (admin_invites_api.py already imports get_current_admin_user *from* admin_api.py).
def get_invite_service(session: Annotated[Session, Depends(get_db_session)]) -> InviteService:
    return InviteService(session)


def get_game_repository(session: Annotated[Session, Depends(get_db_session)]) -> GameRepository:
    return GameRepository(session)


def get_reports_service(session: Annotated[Session, Depends(get_db_session)]) -> ReportsService:
    return ReportsService(session)


def get_notifications_service(
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> NotificationService:
    return NotificationService(session, settings)


# Here (not private to api/admin_notifications_api.py) so main.py's lifespan can also depend on it
# (to start it at boot and shut it down on exit) without importing an api/*_api.py router module
# for it - same reasoning as get_embedding_job_runner above. Reuses get_immich_service()/
# get_ml_service()'s memoized instance rather than constructing a second pair of connection
# pools that would otherwise sit idle next to the ones every request already uses. No
# ImmichService: the runner builds one per notified player (runner._library_of), since birthdays
# and album anniversaries are about each player's own library.
@lru_cache(maxsize=1)
def get_notification_runner() -> NotificationRunner:
    return NotificationRunner(_session_factory, get_settings(), get_ml_service())


def get_game_factory(
    session: Annotated[Session, Depends(get_db_session)],
    immich_service: Annotated[ImmichService, Depends(get_immich_service)],
    ml_service: Annotated[MLService, Depends(get_ml_service)],
    reports_service: Annotated[ReportsService, Depends(get_reports_service)],
) -> GameFactory:
    return GameFactory(session, immich_service, ml_service, GameSettingsService(session), reports_service)


# Here (not private to api/api.py) so api/daily_api.py can also depend on it without api.py <->
# daily_api.py becoming a circular import (api.py already imports daily_api.py's router to mount
# it).
def get_games_service(
    repository: Annotated[GameRepository, Depends(get_game_repository)],
    factory: Annotated[GameFactory, Depends(get_game_factory)],
) -> GamesService:
    return GamesService(repository, factory)


def get_daily_games_service(
    session: Annotated[Session, Depends(get_db_session)],
    repository: Annotated[GameRepository, Depends(get_game_repository)],
    factory: Annotated[GameFactory, Depends(get_game_factory)],
    immich_service: Annotated[ImmichService, Depends(get_immich_service)],
    reports_service: Annotated[ReportsService, Depends(get_reports_service)],
) -> DailyGamesService:
    return DailyGamesService(
        repository,
        factory,
        DailySettingsService(session),
        DailyChallengeService(session, immich_service, reports_service),
    )


def get_scores_service(repository: Annotated[GameRepository, Depends(get_game_repository)]) -> ScoresService:
    return ScoresService(repository)
