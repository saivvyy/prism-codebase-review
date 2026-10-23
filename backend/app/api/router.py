from fastapi import APIRouter
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.memory import router as memory_router
from app.api.repositories import router as repositories_router
from app.api.reviews import router as reviews_router
from app.api.settings import router as settings_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(repositories_router)
api_router.include_router(reviews_router)
api_router.include_router(memory_router)
api_router.include_router(settings_router)

