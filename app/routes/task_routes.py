"""
Task API Routes for AI VMS Task Generation.
"""

import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.services.ai_service import (
    AIServiceAuthError,
    AIServiceConnectionError,
    AIServiceError,
    AIServiceRateLimitError,
    AIServiceTimeoutError,
)
from app.services.task_service import (
    TaskGenerationRequest,
    TaskGenerationResponse,
    TaskService,
)

logger = logging.getLogger("ai_vms_generator.task_routes")
router = APIRouter(prefix="/tasks", tags=["AI VMS Task Generator"])


def get_task_service(settings: Settings = Depends(get_settings)) -> TaskService:
    return TaskService(settings=settings)


@router.post(
    "/generate",
    response_model=TaskGenerationResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate Professional VMS Tasks from Rough Notes",
    description=(
        "Converts rough notes into 4 or 5 candidate task options (title and description only). "
        "Automatically falls back to Nemotron 3.5 Bolt E4B if the primary model reaches rate limits or is unavailable."
    )
)
async def generate_task(
    request: TaskGenerationRequest,
    task_service: TaskService = Depends(get_task_service)
) -> TaskGenerationResponse:
    try:
        return await task_service.generate_tasks(request)

    except (AIServiceAuthError, AIServiceRateLimitError) as e:
        logger.error("AI service credential or rate limit error: %s", str(e))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    except (AIServiceConnectionError, AIServiceTimeoutError) as e:
        logger.error("AI connection or timeout error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error connecting to NVIDIA Nemotron service: {str(e)}"
        )

    except AIServiceError as e:
        logger.error("AI generation failed: %s", str(e))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"AI generation failed: {str(e)}")

    except Exception as e:
        logger.exception("Unexpected error in generate_task: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected internal error occurred: {str(e)}"
        )


@router.get("/health", summary="Health Check")
async def health_check(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "model": settings.NVIDIA_MODEL,
        "primary_model": settings.NVIDIA_MODEL,
        "fallback_model": settings.FALLBACK_MODEL,
        "nvidia_api_key_configured": settings.is_api_key_configured,
        "fallback_configured": settings.is_fallback_configured
    }


@router.get("/info", summary="Model & Capability Information")
async def service_info(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "model": settings.NVIDIA_MODEL,
        "primary_model": settings.NVIDIA_MODEL,
        "fallback_model": settings.FALLBACK_MODEL,
        "fallback_enabled": settings.FALLBACK_ENABLED,
        "provider": "NVIDIA NIM",
        "api_base_url": settings.NVIDIA_BASE_URL,
        "options_range": {"minimum": 4, "maximum": 5, "default": 5},
        "response_styles": [
            "Concise & Direct",
            "Detailed & Comprehensive",
            "Technical & System-Focused",
            "Operational & Impact-Focused",
            "Standard Agile Issue"
        ]
    }
