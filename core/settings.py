"""
Application settings — persisted to data/settings.json.
Falls back to environment variables for defaults, making Railway deployment work
without requiring a pre-existing settings.json.
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import Literal

_SETTINGS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "settings.json"
)


def _env_defaults() -> dict:
    """Read defaults from environment variables (useful for Railway / Docker)."""
    return {
        "weather_service_url": os.environ.get(
            "WEATHER_SERVICE_URL", "http://localhost:8001"
        ),
        "weather_location": os.environ.get("WEATHER_LOCATION", "Valencia"),
    }


@dataclass
class Settings:
    weather_location: str = "Valencia"
    weather_service_url: str = "http://localhost:8001"
    ambient_source: Literal["manual", "live", "climatology"] = "manual"


def _make_default_settings() -> Settings:
    env = _env_defaults()
    return Settings(
        weather_location=env["weather_location"],
        weather_service_url=env["weather_service_url"],
    )


_settings: Settings = _make_default_settings()


def load_settings() -> Settings:
    global _settings
    path = os.path.abspath(_SETTINGS_PATH)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Start from env defaults, then overlay persisted values
            base = asdict(_make_default_settings())
            base.update({k: v for k, v in data.items() if hasattr(Settings, k)})
            _settings = Settings(**base)
        except Exception:
            _settings = _make_default_settings()
    else:
        _settings = _make_default_settings()
    return _settings


def get_settings() -> Settings:
    return _settings


def save_settings(updated: Settings) -> Settings:
    global _settings
    _settings = updated
    path = os.path.abspath(_SETTINGS_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(asdict(_settings), f, indent=2)
    return _settings
