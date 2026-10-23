from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.router import api_router
from app.core.config import get_settings
from app.services.supabase import get_supabase_service

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    supabase = get_supabase_service()
    logger.info(
        "PRism backend startup: Supabase configured=%s, SUPABASE_SERVICE_ROLE_KEY configured: %s",
        supabase.is_configured,
        bool(settings.SUPABASE_SERVICE_ROLE_KEY),
    )
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Persistent organizational memory for AI code reviews",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Direct /health endpoint at root
app.include_router(health_router)

# Versioned API router (/api/v1/...)
app.include_router(api_router, prefix=settings.API_V1_STR)
