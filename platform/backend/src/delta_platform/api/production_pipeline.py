"""Extend the running production API without replacing its application implementation."""

from inspect import signature

from fastapi import APIRouter
from fastapi.routing import APIRoute
from starlette.routing import Mount

from delta_platform.api.app import create_app as create_base_app
from delta_platform.api.pipeline import install
from delta_platform.config import Settings

PIPELINE_ROUTES = {
    ("/api/v1/pipeline/runs", "GET"),
    ("/api/v1/pipeline/options", "GET"),
    ("/api/v1/pipeline/runs", "POST"),
    ("/api/v1/pipeline/runs/{run_id}", "GET"),
    ("/api/v1/pipeline/runs/{run_id}/log/{node_id}", "GET"),
    ("/api/v1/pipeline/runs/{run_id}/answer", "POST"),
}


def _api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif isinstance(getattr(route, "original_route", None), APIRoute):
            yield route
        elif callable(getattr(route, "effective_candidates", None)):
            # Recent FastAPI versions preserve included routers as lazy branches.
            yield from _api_routes(route.effective_candidates())


def create_app(settings=None, engine=None, *, dashboard=None, pipeline=None):
    settings = settings or Settings()
    arguments = {"dashboard": dashboard}
    if "pipeline" in signature(create_base_app).parameters:
        arguments["pipeline"] = pipeline
    app = create_base_app(settings, engine, **arguments)
    routes = list(_api_routes(app.router.routes))
    present = {
        (route.path, method)
        for route in routes
        for method in route.methods
        if (route.path, method) in PIPELINE_ROUTES
    }
    if present:
        if present != PIPELINE_ROUTES:
            raise RuntimeError("The base API has an incomplete pipeline integration")
        return app
    services = [
        route for route in routes if route.path == "/api/v1/services" and "GET" in route.methods
    ]
    if len(services) != 1 or not services[0].dependencies:
        raise RuntimeError("Cannot reuse the existing API authentication dependencies")

    protected = APIRouter(prefix="/api/v1", dependencies=list(services[0].dependencies))
    install(protected, settings, relay=pipeline)
    previous = list(app.router.routes)
    app.include_router(protected)
    additional = list(app.router.routes[len(previous) :])
    insertion = next(
        (
            i
            for i, route in enumerate(previous)
            if isinstance(route, Mount) and route.path in ("", "/")
        ),
        len(previous),
    )
    app.router.routes[:] = previous[:insertion] + additional + previous[insertion:]
    app.openapi_schema = None
    return app
