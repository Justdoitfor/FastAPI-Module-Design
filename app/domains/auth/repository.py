from datetime import datetime, timezone

from pydantic import EmailStr
from sqlalchemy import select, update

from app.db.repository import BaseRepository
from app.domains.auth.models import User, RefreshToken


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: EmailStr) -> User | None:
        stmt = self._select().where(User.email == email)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = self._select().where(RefreshToken.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_family(self, family_id: str) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def list_active_family_ids(self, user_id: int) -> set[str]:
        stmt = select(RefreshToken.family_id).where(RefreshToken.user_id == user_id,
                                                    RefreshToken.revoked_at.is_(None))
        return {
            row[0] for row in (await self.session.execute(stmt)).all()
        }
