"""
Settings routes — GET/POST /settings
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal, Optional
from core.settings import get_settings, save_settings, Settings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsRequest(BaseModel):
    weather_location: Optional[str] = None
    weather_service_url: Optional[str] = None
    ambient_source: Optional[Literal["manual", "live", "climatology"]] = None


@router.get("")
def read_settings():
    """Return current application settings."""
    s = get_settings()
    return {
        "weather_location": s.weather_location,
        "weather_service_url": s.weather_service_url,
        "ambient_source": s.ambient_source,
    }


@router.post("")
def update_settings(req: SettingsRequest):
    """Update and persist application settings."""
    current = get_settings()
    updated = Settings(
        weather_location=req.weather_location if req.weather_location is not None else current.weather_location,
        weather_service_url=req.weather_service_url if req.weather_service_url is not None else current.weather_service_url,
        ambient_source=req.ambient_source if req.ambient_source is not None else current.ambient_source,
    )
    saved = save_settings(updated)
    return {
        "weather_location": saved.weather_location,
        "weather_service_url": saved.weather_service_url,
        "ambient_source": saved.ambient_source,
    }
