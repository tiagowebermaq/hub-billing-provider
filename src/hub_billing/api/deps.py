from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from hub_billing.api.users import User, UserStore
from hub_billing.core.config import Settings
from hub_billing.core.security import decode_token
from hub_billing.domain.permissions import Permission

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_store(request: Request) -> UserStore:
    store: UserStore = request.app.state.users
    return store


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def current_user(
    token: Annotated[str, Depends(oauth2)],
    store: Annotated[UserStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> User:
    try:
        claims = decode_token(token, settings.jwt_secret, settings.jwt_algorithm)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token inválido") from exc
    user = store.get(claims["sub"])
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário inativo")
    return user


def require(permission: Permission) -> Callable[[User], User]:
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if permission not in user.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"permissão necessária: {permission}")
        return user

    return check
