from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    ALEMBIC_DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    model_config = {"env_file": ".env"}
    # class Config:
    #     env_file = ".env"


settings = Settings()
