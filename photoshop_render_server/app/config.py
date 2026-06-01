from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    output_public_base_url: str = "https://bucket.example.com/processed"
    output_upload_base_url: str = "https://bucket.example.com/processed"
    default_jpeg_quality: int = 12
    job_timeout_seconds: int = 600
    worker_heartbeat_interval_seconds: int = 20
    worker_stale_after_seconds: int = 60
    liveness_scan_seconds: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
