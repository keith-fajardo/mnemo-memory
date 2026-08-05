"""Loopback-only lifecycle API and packaged personal dashboard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import resources

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from mnemo_memory.packages.application.services import APP_VERSION, LifecycleService
from mnemo_memory.packages.application.settings import (
    PersonalSettings,
    PersonalSettingsError,
    PersonalSettingsStore,
)

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _web_asset(name: str) -> str:
    return resources.files("mnemo_memory").joinpath(f"resources/web/{name}").read_text("utf-8")


def create_app(
    service: LifecycleService,
    dashboard_status: Callable[[], dict[str, object]] | None = None,
    settings_store: PersonalSettingsStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Mnemo local dashboard", version=APP_VERSION, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def secure_local_response(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_web_asset("index.html"), headers=_SECURITY_HEADERS)

    @app.get("/assets/app.css", response_class=Response)
    def dashboard_css() -> Response:
        return Response(_web_asset("app.css"), media_type="text/css", headers=_SECURITY_HEADERS)

    @app.get("/assets/app.js", response_class=Response)
    def dashboard_javascript() -> Response:
        return Response(
            _web_asset("app.js"), media_type="text/javascript", headers=_SECURITY_HEADERS
        )

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

    @app.get("/api/dashboard")
    def dashboard_api() -> dict[str, object]:
        status = service.status()
        details = {} if dashboard_status is None else dashboard_status()
        return {
            "lifecycle": {
                "initialized": status["initialized"],
                "running": status["running"],
                "schema_version": status["schema_version"],
            },
            "version": APP_VERSION,
            **details,
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        store = settings_store or PersonalSettingsStore(service.config.data_directory)
        try:
            return store.load().to_dict()
        except PersonalSettingsError:
            raise HTTPException(status_code=503, detail="MNEMO_SETTINGS_UNAVAILABLE") from None

    @app.put("/api/settings")
    def put_settings(request: Request, value: dict[str, object]) -> dict[str, object]:
        if request.headers.get("x-mnemo-intent") != "update-settings" or not _same_origin(
            request, service.config.host, service.config.port
        ):
            raise HTTPException(status_code=403, detail="MNEMO_SETTINGS_WRITE_FORBIDDEN")
        store = settings_store or PersonalSettingsStore(service.config.data_directory)
        try:
            return store.save(PersonalSettings.from_dict(value)).to_dict()
        except PersonalSettingsError:
            raise HTTPException(status_code=422, detail="MNEMO_SETTINGS_INVALID") from None

    return app


def _same_origin(request: Request, host: str, port: int) -> bool:
    origin = request.headers.get("origin")
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if host == "::1":
        allowed.add(f"http://[::1]:{port}")
    return origin in allowed
