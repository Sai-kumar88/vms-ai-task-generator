from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI VMS Task Generator"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # Primary NVIDIA Nemotron Model (Nemotron 3.5 Lightning 30B-A3B)
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_MODEL: str = "nvidia/nemotron-3.5-lightning-30b-a3b"

    # Secondary / Fallback AI Provider (Nemotron 3.5 Bolt E4B)
    FALLBACK_ENABLED: bool = True
    FALLBACK_API_KEY: str = ""
    FALLBACK_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    FALLBACK_MODEL: str = "nvidia/nemotron-3.5-bolt-e4b"

    # Inference Hyperparameters
    TEMPERATURE: float = 0.3
    MAX_TOKENS: int = 1800
    TIMEOUT_SECONDS: float = 60.0
    MAX_RETRIES: int = 2

    # Server Settings
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8086
    DEBUG: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @property
    def is_api_key_configured(self) -> bool:
        key = self.NVIDIA_API_KEY.strip()
        return bool(key and not key.startswith("nvapi-your-"))

    @property
    def active_fallback_api_key(self) -> str:
        """Returns the fallback API key, defaulting to primary key if not set."""
        key = self.FALLBACK_API_KEY.strip()
        if key and not key.startswith("nvapi-your-"):
            return key
        return self.NVIDIA_API_KEY.strip()

    @property
    def is_fallback_configured(self) -> bool:
        return self.FALLBACK_ENABLED and bool(self.active_fallback_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
