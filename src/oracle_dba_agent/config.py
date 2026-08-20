"""Runtime configuration, sourced from environment variables / injected secrets."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Oracle connection and agent settings.

    Credentials are never hardcoded: they are read from the process environment,
    which is where Devin secrets, Kubernetes secrets or a local .env file land.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    oracle_user: str = Field(default="", alias="ORACLE_USER")
    oracle_password: SecretStr = Field(default=SecretStr(""), alias="ORACLE_PASSWORD")
    oracle_host: str = Field(default="localhost", alias="ORACLE_HOST")
    oracle_port: int = Field(default=1521, alias="ORACLE_PORT")
    oracle_service: str = Field(default="FREEPDB1", alias="ORACLE_SERVICE")
    oracle_dsn: str = Field(default="", alias="ORACLE_DSN")

    query_timeout_seconds: int = Field(default=30, alias="DBA_QUERY_TIMEOUT")
    pool_min: int = Field(default=1, alias="DBA_POOL_MIN")
    pool_max: int = Field(default=4, alias="DBA_POOL_MAX")

    mcp_server_url: str = Field(default="", alias="DBA_MCP_URL")
    web_host: str = Field(default="0.0.0.0", alias="DBA_WEB_HOST")
    web_port: int = Field(default=8000, alias="DBA_WEB_PORT")

    @property
    def dsn(self) -> str:
        if self.oracle_dsn:
            return self.oracle_dsn
        return f"{self.oracle_host}:{self.oracle_port}/{self.oracle_service}"

    def credentials_present(self) -> bool:
        return bool(self.oracle_user and self.oracle_password.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
