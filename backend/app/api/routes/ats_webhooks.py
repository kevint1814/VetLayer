"""
ATS Webhook endpoints — receive and process events from external Applicant Tracking Systems.

Each endpoint:
  1. Reads raw body + signature header
  2. Verifies HMAC signature
  3. Parses into normalised VetLayer objects
  4. Optionally triggers auto-analysis
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.ats_integration import (
    ATSProvider,
    ATSIntegrationService,
    WebhookEvent,
    WebhookEventType,
    get_parser,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ats", tags=["ATS Integration"])

# ── Webhook secrets (loaded from environment variables) ───────────────
# Production deployments should store per-company secrets in the database.
# These env-based secrets serve as a default for single-tenant setups.
import os
_WEBHOOK_SECRETS: dict[ATSProvider, str] = {
    ATSProvider.GREENHOUSE: os.getenv("WEBHOOK_SECRET_GREENHOUSE", ""),
    ATSProvider.LEVER: os.getenv("WEBHOOK_SECRET_LEVER", ""),
    ATSProvider.ASHBY: os.getenv("WEBHOOK_SECRET_ASHBY", ""),
}


# ═══════════════════════════════════════════════════════════════════════
# Generic webhook receiver (works for any configured provider)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/webhooks/{provider}")
async def receive_webhook(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Universal ATS webhook endpoint.

    URL pattern: POST /api/v1/ats/webhooks/{provider}
    Examples:
      - /api/v1/ats/webhooks/greenhouse
      - /api/v1/ats/webhooks/lever
      - /api/v1/ats/webhooks/ashby
    """
    # Validate provider
    try:
        ats_provider = ATSProvider(provider.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported ATS provider: {provider}. "
                   f"Supported: {', '.join(p.value for p in ATSProvider if p != ATSProvider.GENERIC)}",
        )

    # Read raw body for signature verification
    payload_bytes = await request.body()

    # Get signature from provider-specific header
    service = ATSIntegrationService(db)
    sig_header_name = service.get_signature_header_name(ats_provider)
    signature = request.headers.get(sig_header_name, "")

    # Process
    secret = _WEBHOOK_SECRETS.get(ats_provider, "")
    event = await service.process_webhook(
        provider=ats_provider,
        payload_bytes=payload_bytes,
        signature_header=signature,
        webhook_secret=secret,
    )

    if event is None:
        # Signature failed or duplicate event
        raise HTTPException(status_code=401, detail="Webhook verification failed or duplicate event")

    # Check if we should auto-trigger analysis
    should_analyze = await service.should_auto_analyze(event)

    response = {
        "status": "received",
        "event_id": event.id,
        "event_type": event.event_type.value,
        "provider": ats_provider.value,
        "auto_analyze": should_analyze,
    }

    if event.candidate:
        response["candidate"] = {
            "remote_id": event.candidate.remote_id,
            "name": f"{event.candidate.first_name} {event.candidate.last_name}".strip(),
            "email": event.candidate.email,
            "has_resume": event.candidate.resume_url is not None,
        }

    if event.application:
        response["application"] = {
            "remote_id": event.application.remote_id,
            "job_remote_id": event.application.job_remote_id,
            "job_title": event.application.job_title,
            "stage": event.application.stage,
        }

    if should_analyze:
        response["message"] = (
            "Webhook received — VetLayer analysis will be triggered automatically. "
            "Check the analysis results endpoint for output."
        )
        # TODO: In production, dispatch to background task queue (Celery/ARQ)
        # await trigger_auto_analysis(event, db)

    logger.info(f"ATS webhook processed: {ats_provider.value}/{event.event_type.value}")
    return response


# ═══════════════════════════════════════════════════════════════════════
# Provider status & configuration
# ═══════════════════════════════════════════════════════════════════════

@router.get("/providers")
async def list_ats_providers():
    """List all supported ATS providers and their configuration status."""
    providers = []
    for p in ATSProvider:
        if p == ATSProvider.GENERIC:
            continue
        providers.append({
            "provider": p.value,
            "configured": bool(_WEBHOOK_SECRETS.get(p)),
            "webhook_url": f"/api/v1/ats/webhooks/{p.value}",
            "features": _provider_features(p),
        })
    return {"providers": providers}


@router.get("/providers/{provider}/events")
async def list_provider_events(provider: str):
    """List supported event types for a given ATS provider."""
    try:
        ats_provider = ATSProvider(provider.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    parser = get_parser(ats_provider)

    # Reflect event map from parser
    event_map = getattr(parser, "_EVENT_MAP", {})
    events = [
        {"ats_event": k, "vetlayer_event": v.value}
        for k, v in event_map.items()
    ]

    return {
        "provider": ats_provider.value,
        "supported_events": events,
        "auto_analyze_events": [
            WebhookEventType.APPLICATION_CREATED.value,
            WebhookEventType.APPLICATION_STAGE_CHANGED.value,
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# Webhook test / debug endpoint (dev only)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/webhooks/{provider}/test")
async def test_webhook_parsing(
    provider: str,
    request: Request,
):
    """
    Test endpoint — parses a webhook payload WITHOUT signature verification.
    Only available in DEBUG mode. Useful for integration testing.
    """
    from app.core.config import settings
    if not settings.DEBUG:
        raise HTTPException(status_code=403, detail="Test endpoint only available in DEBUG mode")

    try:
        ats_provider = ATSProvider(provider.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    payload_bytes = await request.body()
    service = ATSIntegrationService()
    event = await service.process_webhook(
        provider=ats_provider,
        payload_bytes=payload_bytes,
        signature_header="",
        webhook_secret="",  # Skip verification for test
    )

    if not event:
        return {"status": "parse_failed", "detail": "Could not parse webhook payload"}

    result = {
        "status": "parsed",
        "event_type": event.event_type.value,
        "provider": ats_provider.value,
    }

    if event.candidate:
        result["normalised_candidate"] = event.candidate.model_dump(
            mode="json", exclude={"raw_data"}
        )
    if event.application:
        result["normalised_application"] = event.application.model_dump(
            mode="json", exclude={"raw_data"}
        )

    return result


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _provider_features(provider: ATSProvider) -> dict:
    """Feature matrix for each ATS provider."""
    features = {
        ATSProvider.GREENHOUSE: {
            "webhooks": True,
            "api_sync": True,
            "resume_download": True,
            "job_sync": True,
            "stage_tracking": True,
            "notes": "Full Harvest API support. Webhooks fire on all candidate lifecycle events.",
        },
        ATSProvider.LEVER: {
            "webhooks": True,
            "api_sync": True,
            "resume_download": True,
            "job_sync": True,
            "stage_tracking": True,
            "notes": "Opportunity-based model. Webhooks cover candidate and application events.",
        },
        ATSProvider.ASHBY: {
            "webhooks": True,
            "api_sync": True,
            "resume_download": True,
            "job_sync": True,
            "stage_tracking": True,
            "notes": "Modern API with strong webhook support. Growing market share.",
        },
        ATSProvider.WORKDAY: {
            "webhooks": False,
            "api_sync": True,
            "resume_download": True,
            "job_sync": True,
            "stage_tracking": True,
            "notes": "No native webhooks — uses polling via Reports as a Service (RaaS). "
                     "Enterprise-grade, requires tenant-specific configuration.",
        },
    }
    return features.get(provider, {})
