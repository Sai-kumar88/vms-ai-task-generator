"""
FastAPI Application Entrypoint for AI VMS Task Generator.
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.task_routes import router as task_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ai_vms_generator.main")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting %s v%s | Model: %s", settings.PROJECT_NAME, settings.VERSION, settings.NVIDIA_MODEL)
    yield
    logger.info("Shutting down %s...", settings.PROJECT_NAME)


def create_application() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Validation Error Handler
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error_type": "ValidationError",
                "message": "Invalid request payload. Please verify your input parameters.",
                "details": exc.errors()
            }
        )

    # HTTP Error Handler
    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "error_type": "HTTPException", "message": exc.detail}
        )

    # Register API Router
    app.include_router(task_router, prefix=settings.API_V1_STR)

    # Mount Static Web UI
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    # Interactive Web UI Route
    @app.get("/ui", include_in_schema=False)
    async def get_ui():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="UI index.html not found")

    # Root Endpoint: Serves Web UI to browsers and JSON to API clients
    @app.get("/", summary="Web UI & API Root")
    async def root(request: Request) -> Any:
        accept = request.headers.get("accept", "")
        user_agent = request.headers.get("user-agent", "").lower()
        index_file = STATIC_DIR / "index.html"

        # If accessed from a web browser, serve index.html directly
        if ("text/html" in accept or "mozilla" in user_agent) and index_file.exists():
            return FileResponse(index_file)

        # For API clients / pytest:
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "operational",
            "model": settings.NVIDIA_MODEL,
            "web_ui": "/",
            "documentation": "/docs",
            "health_check": f"{settings.API_V1_STR}/tasks/health",
            "generate_tasks_endpoint": f"{settings.API_V1_STR}/tasks/generate"
        }

    return app


app = create_application()


if __name__ == "__main__":
    import uvicorn
    app_settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=app_settings.APP_HOST,
        port=app_settings.APP_PORT,
        reload=True
    )
