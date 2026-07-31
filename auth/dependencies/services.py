from fastapi import Depends

from auth.repositories.user_repository import UserRepository
from auth.dependencies.repositories import get_user_repository
from auth.services.auth_service import AuthService


def get_auth_service(user_repository: UserRepository = Depends(get_user_repository)):
    return AuthService(user_repository)
