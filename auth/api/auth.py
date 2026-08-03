from fastapi import Depends, APIRouter
from auth.schemas.auth import UserRegisterRequest
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
