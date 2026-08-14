from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from fastapi import Depends, HTTPException
from auth.core.config import settings
from auth.dependencies.services import get_user_service
from auth.services.user_service import UserService
from auth.core.exceptions import UserNotExist

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_token_payload(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
        )


async def get_current_user(
        payload: dict = Depends(get_token_payload),
        user_service: UserService = Depends(get_user_service)
):
    # 解析token类型
    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=401,
            detail="Invalid access token",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
        )
    user = await user_service.get_user_by_id(int(user_id))
    if not user:
        raise UserNotExist()
    return user
