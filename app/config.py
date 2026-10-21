from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GEMINI_API_KEY: str = ""
    APP_SECRET: str = ""
    HISTORY_PASSWORD: str = ""
    ALLOWED_ORIGINS: str = ""
    MAX_CALL_SECONDS: int = 600
    ALLOW_LOCAL_RECORDINGS: bool = True
    SUMMARY_MODEL: str = "gemini-3.8-flash"
    DATABASE_URL: str = "sqlite:///./vaani.db"
    STORAGE_ENDPOINT_URL: str = ""
    STORAGE_REGION: str = ""
    STORAGE_ACCESS_KEY_ID: str = ""
    STORAGE_SECRET_ACCESS_KEY: str = ""
    STORAGE_BUCKET_NAME: str = ""
    RECORDING_MAX_BYTES: int = 25_000_000


settings = Settings()
