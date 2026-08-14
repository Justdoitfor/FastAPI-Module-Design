from fastapi import APIRouter, Depends

from auth.dependencies.auth import get_current_user

from auth.models.user import User

router = APIRouter(
    prefix="/users",
    tags=["users"]
)


@router.get("/me")
async def get_me(

        current_user: User = Depends(
            get_current_user
        )

):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email
    }
