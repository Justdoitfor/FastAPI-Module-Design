from fastapi import Depends

from auth.repositories.user_repository import UserRepository
from auth.dependencies.repositories import get_user_repository
from auth.services.auth_service import AuthService
from auth.services.token_service import TokenService
from auth.services.user_service import UserService


def get_auth_service(
        user_repository: UserRepository = Depends(get_user_repository),
        token_service: TokenService = Depends(TokenService),
) -> AuthService:
    return AuthService(user_repository=user_repository, token_service=token_service)


def get_user_service(
        user_repository: UserRepository = Depends(get_user_repository),
) -> UserService:
    return UserService(user_repository)


def get_token_service() -> TokenService:
    return TokenService()
