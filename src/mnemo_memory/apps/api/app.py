"""Minimal loopback lifecycle API; no memory or retrieval endpoints exist yet."""

from __future__ import annotations

from fastapi import FastAPI

from mnemo_memory.packages.application.services import APP_VERSION, LifecycleService


def create_app(service: LifecycleService) -> FastAPI:
    app = FastAPI(title="Mnemo local lifecycle", version=APP_VERSION, docs_url=None, redoc_url=None)

    @app.get("/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/ready")
    def ready() -> dict[str, object]:
        status = service.status()
        return {
            "status": "ready" if status["initialized"] else "not_initialized",
            "schema_version": status["schema_version"],
        }

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": APP_VERSION, "profile": service.config.profile}

    @app.get("/process")
    def process() -> dict[str, object]:
        status = service.status()
        return {"running": status["running"], "process": status["process"]}

    return app
