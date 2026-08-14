from fastapi import Depends, APIRouter
from auth.schemas.auth import UserRegisterRequest, UserLoginRequest, TokenResponse, RefreshTokenRequest, \
    RefreshTokenResponse
from auth.schemas.response import ResponseModel
from auth.schemas.user import UserResponse
from auth.services.auth_service import AuthService
from auth.dependencies.services import get_auth_service
from auth.core.response import success

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post("/register", response_model=ResponseModel[UserResponse])
async def register(
        data: UserRegisterRequest,
        service: AuthService = Depends(get_auth_service),
):
    user = await service.register(data)
    return success(user)


@router.post("/login", response_model=ResponseModel[TokenResponse])
async def login(
        data: UserLoginRequest,
        service: AuthService = Depends(get_auth_service),
):
    token = await service.login(data.username, data.password)
    return success(token)


@router.post("/refresh", response_model=ResponseModel[RefreshTokenResponse])
async def refresh_token(
        data: RefreshTokenRequest,
        service: AuthService = Depends(get_auth_service),
):
    result = await service.refresh_access_token(data.refresh_token)
    return success(result)
