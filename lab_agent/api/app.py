"""FastAPI auth gateway; importing this module performs no I/O."""

from fastapi import FastAPI

from .auth_routes import router as auth_router
from .admin_routes import router as admin_router
from .pages import router as auth_pages_router


def create_app() -> FastAPI:
    application = FastAPI(title="LabAgent Auth Gateway")
    application.include_router(auth_router)
    application.include_router(admin_router)
    application.include_router(auth_pages_router)

    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
