"""Application configuration."""
from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "EE AI Fund Management"
    data_dir: str = os.getenv("DATA_DIR", str(BASE_DIR / "data"))
    skills_dir: str = os.getenv("SKILLS_DIR", str(BASE_DIR / "skills_installed"))
    static_dir: str = os.getenv("STATIC_DIR", str(BASE_DIR / "static"))
    db_url_override: str | None = os.getenv("DATABASE_URL")

    @property
    def db_url(self) -> str:
        if self.db_url_override:
            return self.db_url_override
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{Path(self.data_dir) / 'app.db'}"


settings = Settings()
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
Path(settings.skills_dir).mkdir(parents=True, exist_ok=True)
