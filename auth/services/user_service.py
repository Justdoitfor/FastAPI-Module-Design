from auth.models import User
from auth.repositories.user_repository import UserRepository


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def get_user_by_id(self, user_id: int) -> User:
        return await self.repository.get_by_id(user_id)
