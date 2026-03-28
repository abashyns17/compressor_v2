"""
Application settings — persisted to data/settings.json.
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import Literal

_SETTINGS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "settings.json"
)


@dataclass
class Settings:
    weather_location: str = "Valencia"
    weather_service_url: str = "http://localhost:8001"
    ambient_source: Literal["manual", "live", "climatology"] = "manual"


_settings: Settings = Settings()


def load_settings() -> Settings:
    global _settings
    path = os.path.abspath(_SETTINGS_PATH)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            _settings = Settings(**{k: v for k, v in data.items() if hasattr(Settings, k)})
        except Exception:
            _settings = Settings()
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
