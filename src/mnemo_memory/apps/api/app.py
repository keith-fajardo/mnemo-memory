"""Loopback-only lifecycle API and packaged personal dashboard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import resources

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse

from mnemo_memory.packages.application.services import APP_VERSION, LifecycleService

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

    return app
