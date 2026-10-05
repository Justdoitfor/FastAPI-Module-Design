from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    DATABASE_URL: str
    REDIS_URL: str
    RATE_LIMIT_ENABLED: bool = True
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = 'HS256'
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAIL_FROM: str = "noreply@example.com"

    # EXPORT_DIR: str = "./exports"

    S3_ENDPOINT_URL: str | None = None
    S3_PUBLIC_ENDPOINT_URL: str | None = None
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str = "app-files"

    ENV: Literal["local", "test", "staging", "production"] = "local"
    SERVICE_NAME: str = "myapi"
    RELEASE: str = "dev"

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    SLOW_REQUEST_SECONDS: float = 1.0

    METRIC_PORT: int | None = 9100
    WORKER_METRICS_PORT:int | None = 9101

    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SAMPLE_RATIO: float = 1.0


settings = Settings()
