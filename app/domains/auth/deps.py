from typing import Annotated
from fastapi import Depends, HTTPException, status

from fastapi.security import OAuth2PasswordBearer
from app.api.deps import DbDep
from app.domains.auth.exceptions import InactiveUserError, InvalidTokenError
from app.domains.auth.models import User
from app.domains.auth.repository import UserRepository
from app.domains.auth.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


async def get_current_user(
        session: DbDep,
        token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(token)
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise InvalidTokenError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_active_user(user: CurrentUser) -> User:
    if not user.is_active:
        raise InactiveUserError()
    return user


ActiveUser = Annotated[User, Depends(get_current_active_user)]
