"""Auth REST endpoints - registration/login/logout/current-user for this app's own accounts.
Mounted under /auth by api/api.py. Routes don't catch auth_service's domain
exceptions (EmailAlreadyExistsError etc.) - those propagate to the app-level handlers registered
in main.py, same pattern as api/api.py's own routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from api.auth_schemas import (
    ChangePasswordIn,
    LinkImmichIn,
    LoginIn,
    RegisterIn,
    ResetPasswordIn,
    UpdateProfileIn,
    UpdateSkinIn,
    UserOut,
)
from api.deps import get_current_user, get_db_session, get_immich_service
from api.rate_limit import enforce_login_email_limit, limiter
from config import Settings, get_settings
from persistence.users import UserModel
from services import immich_key_vault
from services.auth_service import AuthService
from services.immich import ImmichService
from services.immich.account import verify_api_key

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_NAME = "access_token"


def get_auth_service(session: Annotated[Session, Depends(get_db_session)]) -> AuthService:
    return AuthService(session)


def _cookie_attrs() -> dict[str, object]:
    """Shared between _set_session_cookie and logout - browsers match a cookie for deletion by
    (name, domain, path), but several also expect SameSite/Secure/HttpOnly to match for the
    deletion to reliably take - reading both from here means they can't drift again. SameSite=Lax
    already blocks the cookie from riding along on cross-site POSTs, which covers CSRF for what
    this endpoint set does today. secure comes from settings.cookie_secure - false by default
    since the dev stack and docker-compose.app.yml both serve plain HTTP, set true behind a
    TLS-terminating proxy."""
    return {
        "path": "/",
        "httponly": True,
        "samesite": "lax",
        "secure": get_settings().cookie_secure,
    }


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=get_settings().jwt_expire_days * 24 * 60 * 60,
        **_cookie_attrs(),
    )


@router.post("/register", response_model=UserOut, status_code=201)
# IP-keyed, not the shared limiter's default session-or-IP key: this route is
# what *mints* the session, so keying it by session would let each successful call escape into a
# fresh, unlimited budget of its own (the very next request would carry the brand-new account's
# cookie instead of matching against the count of registrations already made from this network
# origin) - the register limit only means something measured against something the caller can't
# reset by calling the route it protects.
@limiter.limit("3/minute", key_func=get_remote_address)
def register(
    request: Request,
    body: RegisterIn,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserOut:
    user = auth_service.register(
        email=body.email,
        username=body.username,
        full_name=body.full_name,
        password=body.password,
        invite_code=body.invite_code,
    )
    _set_session_cookie(response, auth_service.create_access_token(user))
    return UserOut.from_user(user)


@router.post("/login", response_model=UserOut)
@limiter.limit("5/minute")
def login(
    request: Request,
    body: LoginIn,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserOut:
    # On top of the IP-keyed decorator above (a loose global cap), this bounds
    # attempts against one specific email regardless of which IP/session they come from - see
    # api/rate_limit.py's enforce_login_email_limit for why the decorator alone can't do this.
    enforce_login_email_limit(body.email, request.url.path)
    user = auth_service.authenticate(body.email, body.password)
    _set_session_cookie(response, auth_service.create_access_token(user))
    return UserOut.from_user(user)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME, **_cookie_attrs())


@router.post("/reset-password", status_code=204)
@limiter.limit("5/minute")
def reset_password(
    request: Request,
    body: ResetPasswordIn,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    auth_service.reset_password(body.token, body.new_password)


@router.get("/me", response_model=UserOut)
def get_me(user: Annotated[UserModel, Depends(get_current_user)]) -> UserOut:
    return UserOut.from_user(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: UpdateProfileIn,
    user: Annotated[UserModel, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserOut:
    updated = auth_service.update_profile(user, username=body.username, full_name=body.full_name)
    return UserOut.from_user(updated)


@router.patch("/me/password", response_model=UserOut)
@limiter.limit("5/minute")
def change_password(
    request: Request,
    body: ChangePasswordIn,
    response: Response,
    user: Annotated[UserModel, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserOut:
    updated = auth_service.change_password(user, body.current_password, body.new_password)
    # Re-issue the cookie: change_password() just set password_changed_at, which would otherwise
    # revoke the caller's own current session on its very next request.
    _set_session_cookie(response, auth_service.create_access_token(updated))
    return UserOut.from_user(updated)


@router.put("/me/skin", response_model=UserOut)
def update_skin(
    body: UpdateSkinIn,
    user: Annotated[UserModel, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    immich_service: Annotated[ImmichService, Depends(get_immich_service)],
) -> UserOut:
    if body.person_id is not None:
        found = immich_service.get_persons(ids=frozenset({body.person_id}), limit=1)
        if not found:
            raise HTTPException(status_code=404, detail=f"person {body.person_id} not found")
    updated = auth_service.set_skin(user, body.person_id)
    return UserOut.from_user(updated)


@router.put("/me/immich", response_model=UserOut)
# Each call makes two outbound requests to Immich - and a wrong key is worth slowing down.
@limiter.limit("5/minute")
def link_immich(
    request: Request,
    body: LinkImmichIn,
    user: Annotated[UserModel, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserOut:
    """Links the caller's own Immich account: validates the key against Immich (which user it
    belongs to, and that it has every permission this app needs - services/immich/account.py),
    then stores it encrypted. From then on everything the caller plays is scoped to that Immich
    user's library. Re-linking replaces the previous key."""
    api_key = body.api_key.strip()
    account = verify_api_key(settings, api_key)
    updated = auth_service.link_immich(
        user,
        immich_user_id=account.user_id,
        encrypted_api_key=immich_key_vault.encrypt(api_key, settings),
        name=account.name,
        email=account.email,
    )
    return UserOut.from_user(updated)


@router.delete("/me/immich", response_model=UserOut)
def unlink_immich(
    user: Annotated[UserModel, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserOut:
    return UserOut.from_user(auth_service.unlink_immich(user))
