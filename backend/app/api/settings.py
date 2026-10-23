from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from app.core.config import get_settings
from app.services.github import GitHubService, get_github_service
from app.services.groq import GroqService, get_groq_service
from app.services.memory import HindsightMemoryService, get_hindsight_service
from app.services.supabase import SupabaseService, get_supabase_service

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("/status", response_model=Dict[str, Any])
async def get_system_status(
    github: GitHubService = Depends(get_github_service),
    memory: HindsightMemoryService = Depends(get_hindsight_service),
    groq: GroqService = Depends(get_groq_service),
    supabase: SupabaseService = Depends(get_supabase_service),
) -> Dict[str, Any]:
    """Returns platform health and connection status for each integration without exposing secrets."""
    settings = get_settings()

    # Safely probe GitHub user if configured
    github_user = None
    if github.is_configured:
        try:
            gh_user = await github.get_authenticated_user()
            github_user = {
                "login": gh_user.login,
                "name": gh_user.name,
                "avatar_url": gh_user.avatar_url,
            }
        except Exception:
            github_user = {"login": "token_active"}

    return {
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "services": {
            "github": {
                "configured": github.is_configured,
                "user": github_user,
                "api_endpoint": "https://api.github.com",
            },
            "supabase": {
                "configured": supabase.is_configured,
                "url": settings.SUPABASE_URL.split("@")[-1] if settings.SUPABASE_URL else None,
                "storage_mode": "live_postgres" if supabase.is_configured else "in_memory_state",
            },
            "hindsight": {
                "configured": memory.is_configured,
                "base_url": settings.HINDSIGHT_BASE_URL,
                "default_bank_id": settings.HINDSIGHT_DEFAULT_BANK_ID,
            },
            "groq": {
                "configured": groq.is_configured,
                "reasoning_model": settings.GROQ_REASONING_MODEL,
                "lightweight_model": settings.GROQ_LIGHTWEIGHT_MODEL,
            },
            "tree_sitter": {
                "active": True,
                "supported_languages": ["python", "javascript", "typescript", "java", "cpp"],
            },
        },
    }
