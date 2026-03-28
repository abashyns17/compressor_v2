"""
FastAPI application entry point — Sullair LS110 Compressor Backend
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.routes import state, scenarios, inject, analysis, predict, logs, diagnose
from api.routes import settings as settings_route
from api.routes import weather as weather_route
from simulation.scenario_engine import build_scenario
from core.settings import load_settings

import api.routes.state as state_module


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_settings()
    initial_state = build_scenario("normal")
    state_module.set_state(initial_state)
    print("Sullair LS110 backend started — normal scenario loaded")
    print(f"  Machine hours: {initial_state.total_hours:.0f}")
    print(f"  Load: {initial_state.load_pct:.0f}%  Ambient: {initial_state.ambient_f:.0f}°F")
    yield
    print("Shutting down")


app = FastAPI(
    title="Sullair LS110 — ProActive Agents Backend",
    description=(
        "Physics-informed digital twin simulation for the Sullair LS110 "
        "rotary screw air compressor."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(state.router)
app.include_router(scenarios.router)
app.include_router(inject.router)
app.include_router(analysis.router)
app.include_router(predict.router)
app.include_router(logs.router)
app.include_router(diagnose.router)
app.include_router(settings_route.router)
app.include_router(weather_route.router)


@app.get("/")
def root():
    return {
        "service": "Sullair LS110 ProActive Agents Backend",
        "version": "0.1.0",
        "docs": "/docs",
        "endpoints": {
            "state":     "/state/          — current sensors and component health",
            "scenarios": "/scenarios/      — load scenarios, advance time",
            "inject":    "/inject/         — fault injection and component control",
            "analysis":  "/analysis/       — correlations, trends, risk assessment",
            "predict":   "/predict/        — forward projection, envelope, optimizer",
            "logs":      "/logs/           — sensor history for graph generation",
            "diagnose":  "/diagnose/       — symptom-driven diagnostic engine",
            "settings":  "/settings/       — weather service and ambient source config",
            "weather":   "/weather/        — blended ambient temperature profile",
        },
    }
