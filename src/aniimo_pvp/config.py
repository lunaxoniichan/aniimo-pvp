"""Settings from config.yaml + .env (DISCORD_TOKEN)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

from .domain.draft import DEFAULT_ORDER, validate_order


class ConfigError(RuntimeError):
    pass


class Channels(BaseModel):
    admin: int = 0
    announce: int = 0
    matches: int = 0
    live_draft: int = 0
    friendly: int = 0  # where /duel works and friendly draft threads open (0 = friendly duels off)


class Timers(BaseModel):
    draft_step_seconds: float = 45
    side_choice_seconds: float = 120
    noshow_reminder_minutes: float = 10
    noshow_escalate_minutes: float = 15
    noshow_extend_minutes: float = 10
    auto_confirm_minutes: float = 10
    rematch_offer_seconds: float = 120
    thread_archive_minutes: float = 10
    challenge_minutes: float = 10  # a /duel challenge expires if not accepted in time
    friendly_idle_minutes: float = 30  # a friendly duel ends by itself after a finished draft and no activity


class Paths(BaseModel):
    database: str = "data/aniimo.db"
    roster_csv: str = "data/roster.csv"
    images_dir: str = "assets/aniimo"
    elements_dir: str = "assets/elements"
    fonts_dir: str = "assets/fonts"
    logs_dir: str = "logs"
    emoji_cache: str = "data/emoji_cache.json"
    backups_dir: str = "data/backups"


class Settings(BaseModel):
    token: str = ""
    guild_id: int = 0
    admin_role_id: int = 0
    channels: Channels = Field(default_factory=Channels)
    timers: Timers = Field(default_factory=Timers)
    paths: Paths = Field(default_factory=Paths)
    dev_mode: bool = False
    upload_emojis: bool = True
    draft_order: tuple[tuple[str, str], ...] = DEFAULT_ORDER

    @field_validator("draft_order", mode="before")
    @classmethod
    def _order(cls, v: Any) -> tuple[tuple[str, str], ...]:
        if v is None:
            return DEFAULT_ORDER
        return validate_order(v)

    def problems(self) -> list[str]:
        """Settings that must be filled in before the bot can run against Discord."""
        out = []
        if not self.token or self.token.startswith("paste-"):
            out.append("DISCORD_TOKEN is missing (put it in .env)")
        if not self.guild_id:
            out.append("guild_id is missing in config.yaml")
        if not self.admin_role_id:
            out.append("admin_role_id is missing in config.yaml")
        for name in ("admin", "announce", "matches"):
            if not getattr(self.channels, name):
                out.append(f"channels.{name} is missing in config.yaml")
        return out

    def resolve(self, base: Path) -> Settings:
        """Make relative paths relative to the project folder."""
        paths = {
            k: str((base / v).resolve()) if not Path(v).is_absolute() else v for k, v in self.paths.model_dump().items()
        }
        return self.model_copy(update={"paths": Paths(**paths)})


def load_settings(config_path: str | Path = "config.yaml", env_path: str | Path = ".env") -> Settings:
    config_path, env_path = Path(config_path), Path(env_path)
    if env_path.exists():
        load_dotenv(env_path, override=False)
    if not config_path.exists():
        raise ConfigError(f"{config_path} not found. Copy config.example.yaml to config.yaml and fill it in.")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{config_path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must be a mapping of settings")
    data["token"] = os.environ.get("DISCORD_TOKEN", data.get("token", ""))
    try:
        settings = Settings(**data)
    except ValidationError as e:
        lines = [f"  {'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ConfigError(f"{config_path} has invalid settings:\n" + "\n".join(lines)) from e
    return settings.resolve(config_path.resolve().parent)
