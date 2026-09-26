from typing import Annotated
from fastapi import APIRouter, Depends, status

from app.api.deps import DbDep
from app.domains.auth.deps import ActiveUser
from app.domains.auth.schemas import RefreshRequest, TokenPair, UserCreate, UserLogin, UserRead
from app.domains.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(session: DbDep) -> AuthService:
    return AuthService(session)


ServiceDep = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, service: ServiceDep):
    return await service.register(data)


@router.post("/login", response_model=TokenPair, status_code=status.HTTP_200_OK)
async def login(data: UserLogin, service: ServiceDep):
    return await service.login(data.email, data.password)


@router.post("/refresh", response_model=TokenPair, status_code=status.HTTP_200_OK)
async def refresh(data: RefreshRequest, service: ServiceDep):
    return await service.refresh(data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshRequest, service: ServiceDep):
    return await service.logout(data.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: ActiveUser, service: ServiceDep):
    await service.logout_all_devices(user.id)


@router.get("/me", response_model=UserRead)
async def get_me(user: ActiveUser):
    return user
