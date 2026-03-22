"""
ATS Integration Layer — unified webhook & API adapter for major Applicant Tracking Systems.

Supports:
  • Greenhouse  (webhooks + Harvest API)
  • Lever        (webhooks + Lever API v1)
  • Workday      (Workday Recruiting REST)
  • Ashby        (webhooks + REST API)
  • Generic      (extensible base for any ATS via webhook signature)

Design goals:
  1. Normalised data objects — every ATS maps into a single VetLayer schema
  2. HMAC-SHA256 webhook signature verification per provider
  3. Event-driven — on "candidate.applied" or "application.created", auto-trigger
     VetLayer analysis pipeline
  4. Idempotent processing — dedup on (provider + remote_id)
  5. Async throughout — no blocking I/O
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════

class ATSProvider(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    GENERIC = "generic"


class WebhookEventType(str, Enum):
    CANDIDATE_CREATED = "candidate.created"
    CANDIDATE_UPDATED = "candidate.updated"
    APPLICATION_CREATED = "application.created"
    APPLICATION_UPDATED = "application.updated"
    APPLICATION_STAGE_CHANGED = "application.stage_changed"
    OFFER_CREATED = "offer.created"
    CANDIDATE_HIRED = "candidate.hired"
    CANDIDATE_REJECTED = "candidate.rejected"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Normalised data objects
# ═══════════════════════════════════════════════════════════════════════

class NormalisedCandidate(BaseModel):
    """Unified candidate representation across all ATS providers."""
    remote_id: str
    provider: ATSProvider
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    current_title: str = ""
    current_company: str = ""
    location: str = ""
    linkedin_url: str = ""
    resume_url: Optional[str] = None
    resume_content_type: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    raw_data: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NormalisedApplication(BaseModel):
    """Unified application representation."""
    remote_id: str
    provider: ATSProvider
    candidate_remote_id: str
    job_remote_id: str
    job_title: str = ""
    stage: str = ""
    status: str = ""  # "active", "rejected", "hired"
    applied_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class NormalisedJob(BaseModel):
    """Unified job representation."""
    remote_id: str
    provider: ATSProvider
    title: str = ""
    department: str = ""
    team: str = ""
    location: str = ""
    employment_type: str = ""  # "full_time", "part_time", "contract"
    status: str = ""  # "open", "closed", "draft"
    description_html: str = ""
    description_text: str = ""
    requirements: list[str] = Field(default_factory=list)
    remote_created_at: Optional[datetime] = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class WebhookEvent(BaseModel):
    """Parsed, provider-agnostic webhook event."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: ATSProvider
    event_type: WebhookEventType
    remote_event_id: Optional[str] = None
    candidate: Optional[NormalisedCandidate] = None
    application: Optional[NormalisedApplication] = None
    job: Optional[NormalisedJob] = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature_verified: bool = False


# ═══════════════════════════════════════════════════════════════════════
# Webhook signature verification
# ═══════════════════════════════════════════════════════════════════════

def verify_webhook_signature(
    payload_bytes: bytes,
    signature_header: str,
    secret: str,
    provider: ATSProvider,
) -> bool:
    """
    Verify webhook HMAC signature for the given ATS provider.

    Each provider uses a slightly different signing scheme:
      • Greenhouse: SHA-256 HMAC, header = "Signature"
      • Lever:      SHA-256 HMAC, header = "X-Lever-Signature"
      • Ashby:      SHA-256 HMAC, header = "X-Ashby-Signature"
      • Generic:    SHA-256 HMAC, header = "X-Webhook-Signature"
    """
    if not signature_header or not secret:
        return False

    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        # Some providers prefix with "sha256=", strip it
        actual = signature_header.removeprefix("sha256=").strip()

        return hmac.compare_digest(expected, actual)
    except Exception as e:
        logger.warning(f"Webhook signature verification failed for {provider}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# Provider-specific parsers
# ═══════════════════════════════════════════════════════════════════════

class BaseATSParser:
    """Base class for ATS-specific payload parsing."""

    provider: ATSProvider = ATSProvider.GENERIC

    def parse_event_type(self, payload: dict) -> WebhookEventType:
        raise NotImplementedError

    def parse_candidate(self, data: dict) -> NormalisedCandidate:
        raise NotImplementedError

    def parse_application(self, data: dict) -> NormalisedApplication:
        raise NotImplementedError

    def parse_job(self, data: dict) -> NormalisedJob:
        raise NotImplementedError

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        event_type = self.parse_event_type(payload)
        event = WebhookEvent(
            provider=self.provider,
            event_type=event_type,
            raw_payload=payload,
        )

        try:
            if event_type in (
                WebhookEventType.CANDIDATE_CREATED,
                WebhookEventType.CANDIDATE_UPDATED,
            ):
                event.candidate = self.parse_candidate(payload)
            elif event_type in (
                WebhookEventType.APPLICATION_CREATED,
                WebhookEventType.APPLICATION_UPDATED,
                WebhookEventType.APPLICATION_STAGE_CHANGED,
            ):
                event.application = self.parse_application(payload)
                # Many ATS include candidate data in application events
                try:
                    event.candidate = self.parse_candidate(payload)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to parse {event_type} payload from {self.provider}: {e}")

        return event


class GreenhouseParser(BaseATSParser):
    """
    Greenhouse Harvest API & webhook parser.

    Webhook docs: https://developers.greenhouse.io/webhooks.html
    Harvest API:  https://developers.greenhouse.io/harvest.html
    """

    provider = ATSProvider.GREENHOUSE

    # Greenhouse event action → VetLayer event type
    _EVENT_MAP = {
        "new_candidate_application": WebhookEventType.APPLICATION_CREATED,
        "candidate_stage_change": WebhookEventType.APPLICATION_STAGE_CHANGED,
        "candidate_hired": WebhookEventType.CANDIDATE_HIRED,
        "candidate_rejected": WebhookEventType.CANDIDATE_REJECTED,
        "offer_created": WebhookEventType.OFFER_CREATED,
    }

    def parse_event_type(self, payload: dict) -> WebhookEventType:
        action = payload.get("action", "")
        return self._EVENT_MAP.get(action, WebhookEventType.UNKNOWN)

    def parse_candidate(self, payload: dict) -> NormalisedCandidate:
        # Greenhouse nests under payload.application.candidate or payload.candidate
        app_data = payload.get("payload") or {}
        app_obj = app_data.get("application") or {}
        cand = app_data.get("candidate") or app_obj.get("candidate") or {}

        # Extract resume attachment URL
        resume_url = None
        resume_ct = None
        for att in cand.get("attachments") or []:
            if isinstance(att, dict) and att.get("type") == "resume":
                resume_url = att.get("url")
                resume_ct = att.get("content_type")
                break

        emails = cand.get("email_addresses") or []
        phones = cand.get("phone_numbers") or []
        socials = cand.get("social_media_addresses") or []
        tags_raw = cand.get("tags") or []

        return NormalisedCandidate(
            remote_id=str(cand.get("id", "")),
            provider=self.provider,
            first_name=cand.get("first_name", ""),
            last_name=cand.get("last_name", ""),
            email=_first_of_type(emails, "personal") or _first_value(emails),
            phone=_first_value(phones),
            current_title=cand.get("title", ""),
            current_company=cand.get("company", ""),
            linkedin_url=_extract_social(socials, "linkedin"),
            resume_url=resume_url,
            resume_content_type=resume_ct,
            tags=[t.get("name", "") for t in tags_raw if isinstance(t, dict)],
            source=_nested_get(app_data, "source", "public_name") or "",
            raw_data=cand,
        )

    def parse_application(self, payload: dict) -> NormalisedApplication:
        app = payload.get("payload", {}).get("application", payload.get("payload", {}))
        cand = app.get("candidate", {})

        jobs = app.get("jobs", [])
        job_id = str(jobs[0].get("id", "")) if jobs else ""
        job_title = jobs[0].get("name", "") if jobs else ""

        current_stage = app.get("current_stage", {})

        return NormalisedApplication(
            remote_id=str(app.get("id", "")),
            provider=self.provider,
            candidate_remote_id=str(cand.get("id", "")),
            job_remote_id=job_id,
            job_title=job_title,
            stage=current_stage.get("name", ""),
            status=app.get("status", "active"),
            applied_at=_parse_iso(app.get("applied_at")),
            raw_data=app,
        )

    def parse_job(self, data: dict) -> NormalisedJob:
        """Parse from Harvest API /jobs/{id} response."""
        dept = data.get("departments", [{}])
        offices = data.get("offices", [{}])

        return NormalisedJob(
            remote_id=str(data.get("id", "")),
            provider=self.provider,
            title=data.get("name", ""),
            department=dept[0].get("name", "") if dept else "",
            location=offices[0].get("name", "") if offices else "",
            status="open" if data.get("status") == "open" else "closed",
            description_html=data.get("content", ""),
            remote_created_at=_parse_iso(data.get("created_at")),
            raw_data=data,
        )


class LeverParser(BaseATSParser):
    """
    Lever API v1 & webhook parser.

    Docs: https://hire.lever.co/developer/documentation
    """

    provider = ATSProvider.LEVER

    _EVENT_MAP = {
        "candidateCreated": WebhookEventType.CANDIDATE_CREATED,
        "candidateStageChange": WebhookEventType.APPLICATION_STAGE_CHANGED,
        "candidateHired": WebhookEventType.CANDIDATE_HIRED,
        "candidateArchived": WebhookEventType.CANDIDATE_REJECTED,
        "applicationCreated": WebhookEventType.APPLICATION_CREATED,
    }

    def parse_event_type(self, payload: dict) -> WebhookEventType:
        event = payload.get("event", "")
        return self._EVENT_MAP.get(event, WebhookEventType.UNKNOWN)

    def parse_candidate(self, payload: dict) -> NormalisedCandidate:
        data = payload.get("data") or {}
        # Lever calls candidates "opportunities"
        cand = data.get("candidate") or data

        emails = cand.get("emails") or []
        phones = cand.get("phones") or []
        links = cand.get("links") or []

        resume_url = None
        resume_files = cand.get("resumeFiles") or cand.get("files") or []
        for r in resume_files:
            if isinstance(r, dict) and r.get("downloadUrl"):
                resume_url = r["downloadUrl"]
                break

        name = cand.get("name") or ""
        name_parts = name.split(" ", 1)

        return NormalisedCandidate(
            remote_id=str(cand.get("id", "")),
            provider=self.provider,
            first_name=name_parts[0] if name_parts else "",
            last_name=name_parts[1] if len(name_parts) > 1 else "",
            email=emails[0] if emails and isinstance(emails[0], str) else "",
            phone=phones[0].get("value", "") if phones and isinstance(phones[0], dict) else "",
            current_title=cand.get("headline", ""),
            location=cand.get("location", ""),
            linkedin_url=next((lnk for lnk in links if isinstance(lnk, str) and "linkedin" in lnk.lower()), ""),
            resume_url=resume_url,
            tags=cand.get("tags") or [],
            source=_nested_get(cand, "sources", 0) or "",
            raw_data=cand,
        )

    def parse_application(self, payload: dict) -> NormalisedApplication:
        data = payload.get("data", {})
        opp = data.get("opportunity", data)

        postings = opp.get("applications") or []
        posting = postings[0] if postings else {}

        return NormalisedApplication(
            remote_id=str(opp.get("id", "")),
            provider=self.provider,
            candidate_remote_id=str(opp.get("contact", "")),
            job_remote_id=str(posting.get("posting", "")),
            job_title=posting.get("postingTitle", ""),
            stage=opp.get("stage", ""),
            status="active" if not opp.get("archived") else "rejected",
            applied_at=_parse_epoch_ms(opp.get("createdAt")),
            raw_data=opp,
        )

    def parse_job(self, data: dict) -> NormalisedJob:
        categories = data.get("categories", {})
        return NormalisedJob(
            remote_id=str(data.get("id", "")),
            provider=self.provider,
            title=data.get("text", ""),
            department=categories.get("department", ""),
            team=categories.get("team", ""),
            location=categories.get("location", ""),
            employment_type=categories.get("commitment", ""),
            status="open" if data.get("state") == "published" else "closed",
            description_html=data.get("content", {}).get("description", ""),
            remote_created_at=_parse_epoch_ms(data.get("createdAt")),
            raw_data=data,
        )


class AshbyParser(BaseATSParser):
    """
    Ashby webhook & API parser.

    Docs: https://developers.ashbyhq.com
    """

    provider = ATSProvider.ASHBY

    _EVENT_MAP = {
        "candidate.created": WebhookEventType.CANDIDATE_CREATED,
        "application.submit": WebhookEventType.APPLICATION_CREATED,
        "application.stageChange": WebhookEventType.APPLICATION_STAGE_CHANGED,
        "application.hireDecision": WebhookEventType.CANDIDATE_HIRED,
    }

    def parse_event_type(self, payload: dict) -> WebhookEventType:
        action = payload.get("action", payload.get("event", ""))
        return self._EVENT_MAP.get(action, WebhookEventType.UNKNOWN)

    def parse_candidate(self, payload: dict) -> NormalisedCandidate:
        data = payload.get("data", {})
        cand = data.get("candidate", data)

        return NormalisedCandidate(
            remote_id=str(cand.get("id", "")),
            provider=self.provider,
            first_name=cand.get("firstName", cand.get("name", "").split(" ")[0]),
            last_name=cand.get("lastName", " ".join(cand.get("name", "").split(" ")[1:])),
            email=cand.get("primaryEmailAddress", {}).get("value", "")
                  if isinstance(cand.get("primaryEmailAddress"), dict)
                  else cand.get("primaryEmailAddress", ""),
            phone=cand.get("primaryPhoneNumber", {}).get("value", "")
                  if isinstance(cand.get("primaryPhoneNumber"), dict)
                  else "",
            current_title=cand.get("headline", ""),
            location=cand.get("location", {}).get("name", "")
                     if isinstance(cand.get("location"), dict)
                     else cand.get("location", ""),
            linkedin_url=next(
                (s.get("url", "") for s in cand.get("socialLinks", [])
                 if "linkedin" in s.get("url", "").lower()),
                "",
            ),
            source=cand.get("source", {}).get("title", "")
                   if isinstance(cand.get("source"), dict) else "",
            raw_data=cand,
        )

    def parse_application(self, payload: dict) -> NormalisedApplication:
        data = payload.get("data", {})
        app = data.get("application", data)

        return NormalisedApplication(
            remote_id=str(app.get("id", "")),
            provider=self.provider,
            candidate_remote_id=str(app.get("candidateId", "")),
            job_remote_id=str(app.get("jobId", "")),
            job_title=app.get("job", {}).get("title", "") if isinstance(app.get("job"), dict) else "",
            stage=app.get("currentInterviewStage", {}).get("title", "")
                  if isinstance(app.get("currentInterviewStage"), dict) else "",
            status=app.get("status", "active"),
            raw_data=app,
        )

    def parse_job(self, data: dict) -> NormalisedJob:
        return NormalisedJob(
            remote_id=str(data.get("id", "")),
            provider=self.provider,
            title=data.get("title", ""),
            department=data.get("department", {}).get("name", "")
                       if isinstance(data.get("department"), dict) else "",
            team=data.get("team", {}).get("name", "")
                 if isinstance(data.get("team"), dict) else "",
            location=data.get("location", {}).get("name", "")
                     if isinstance(data.get("location"), dict) else data.get("location", ""),
            status="open" if data.get("status") == "Published" else "closed",
            description_html=data.get("descriptionHtml", ""),
            description_text=data.get("descriptionPlain", ""),
            raw_data=data,
        )


class WorkdayParser(BaseATSParser):
    """
    Workday Recruiting REST API parser.

    Workday doesn't have native webhooks — uses polling or RaaS (Reports as a Service).
    This parser handles the REST API response format for /candidates and /jobRequisitions.
    """

    provider = ATSProvider.WORKDAY

    def parse_event_type(self, payload: dict) -> WebhookEventType:
        # Workday uses polling, so events are synthesised
        event = payload.get("_vetlayer_event_type", "")
        _valid_values = {e.value for e in WebhookEventType}
        return WebhookEventType(event) if event in _valid_values else WebhookEventType.UNKNOWN

    def parse_candidate(self, payload: dict) -> NormalisedCandidate:
        data = payload.get("data", payload)
        cand = data.get("candidate", data)
        descriptor = cand.get("descriptor", "")
        name_parts = descriptor.split(", ") if ", " in descriptor else descriptor.split(" ")

        return NormalisedCandidate(
            remote_id=str(cand.get("id", cand.get("wid", ""))),
            provider=self.provider,
            first_name=name_parts[-1] if len(name_parts) > 1 else name_parts[0],
            last_name=name_parts[0] if len(name_parts) > 1 else "",
            email=cand.get("emailAddress", ""),
            phone=cand.get("phoneNumber", ""),
            current_title=cand.get("jobTitle", ""),
            current_company=cand.get("employer", ""),
            location=cand.get("location", {}).get("descriptor", "")
                     if isinstance(cand.get("location"), dict) else "",
            raw_data=cand,
        )

    def parse_application(self, payload: dict) -> NormalisedApplication:
        data = payload.get("data", payload)
        app = data.get("jobApplication", data)

        return NormalisedApplication(
            remote_id=str(app.get("id", app.get("wid", ""))),
            provider=self.provider,
            candidate_remote_id=str(app.get("candidate", {}).get("id", ""))
                                if isinstance(app.get("candidate"), dict) else "",
            job_remote_id=str(app.get("jobRequisition", {}).get("id", ""))
                          if isinstance(app.get("jobRequisition"), dict) else "",
            job_title=app.get("jobRequisition", {}).get("descriptor", "")
                      if isinstance(app.get("jobRequisition"), dict) else "",
            stage=app.get("stage", {}).get("descriptor", "")
                  if isinstance(app.get("stage"), dict) else "",
            status=app.get("status", "active"),
            raw_data=app,
        )

    def parse_job(self, data: dict) -> NormalisedJob:
        return NormalisedJob(
            remote_id=str(data.get("id", data.get("wid", ""))),
            provider=self.provider,
            title=data.get("descriptor", data.get("title", "")),
            department=data.get("supervisoryOrganization", {}).get("descriptor", "")
                       if isinstance(data.get("supervisoryOrganization"), dict) else "",
            location=data.get("primaryLocation", {}).get("descriptor", "")
                     if isinstance(data.get("primaryLocation"), dict) else "",
            employment_type=data.get("jobType", {}).get("descriptor", "")
                            if isinstance(data.get("jobType"), dict) else "",
            status="open" if data.get("status") == "Open" else "closed",
            raw_data=data,
        )


# ═══════════════════════════════════════════════════════════════════════
# Parser registry
# ═══════════════════════════════════════════════════════════════════════

_PARSERS: dict[ATSProvider, BaseATSParser] = {
    ATSProvider.GREENHOUSE: GreenhouseParser(),
    ATSProvider.LEVER: LeverParser(),
    ATSProvider.ASHBY: AshbyParser(),
    ATSProvider.WORKDAY: WorkdayParser(),
}


def get_parser(provider: ATSProvider) -> BaseATSParser:
    parser = _PARSERS.get(provider)
    if not parser:
        raise ValueError(f"No parser registered for ATS provider: {provider}")
    return parser


# ═══════════════════════════════════════════════════════════════════════
# Helper utilities
# ═══════════════════════════════════════════════════════════════════════

def _first_of_type(items: list[dict], type_name: str) -> str:
    """Extract first value from a list of {type, value} dicts matching type."""
    for item in items:
        if isinstance(item, dict) and item.get("type", "").lower() == type_name.lower():
            return item.get("value", "")
    return ""


def _first_value(items: list) -> str:
    """Extract first value from a list of dicts or strings."""
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        return first.get("value", first.get("email", ""))
    return str(first)


def _extract_social(items: list[dict], platform: str) -> str:
    """Extract social media URL by platform name."""
    for item in items:
        if isinstance(item, dict) and platform.lower() in item.get("url", "").lower():
            return item.get("url", "")
    return ""


def _nested_get(data: dict, *keys) -> Any:
    """Safely traverse nested dict/list."""
    current = data
    for key in keys:
        try:
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, (list, tuple)) and isinstance(key, int):
                current = current[key]
            else:
                return None
        except (IndexError, KeyError, TypeError):
            return None
    return current


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 datetime string."""
    if not value:
        return None
    try:
        # Handle Zulu suffix
        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _parse_epoch_ms(value: Optional[int]) -> Optional[datetime]:
    """Parse millisecond epoch timestamp."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


# ═══════════════════════════════════════════════════════════════════════
# High-level integration service
# ═══════════════════════════════════════════════════════════════════════

class ATSIntegrationService:
    """
    Orchestrates ATS webhook processing and candidate/job sync.

    Usage:
        service = ATSIntegrationService(db_session)
        event = await service.process_webhook(provider, payload, signature, secret)
        if event and event.candidate:
            # Auto-trigger VetLayer analysis
            ...
    """

    def __init__(self, db_session=None):
        self.db = db_session
        self._processed_events: dict[str, bool] = {}  # Ordered dict for LRU dedup (use Redis in production)

    async def process_webhook(
        self,
        provider: ATSProvider,
        payload_bytes: bytes,
        signature_header: str = "",
        webhook_secret: str = "",
    ) -> Optional[WebhookEvent]:
        """
        Process an incoming ATS webhook:
          1. Verify signature
          2. Parse payload into normalised objects
          3. Dedup by remote event ID
          4. Return WebhookEvent for downstream processing
        """
        # 1. Signature verification (skip for Workday — polling-based)
        if provider != ATSProvider.WORKDAY and webhook_secret:
            if not verify_webhook_signature(payload_bytes, signature_header, webhook_secret, provider):
                logger.warning(f"Invalid webhook signature from {provider}")
                return None

        # 2. Parse payload
        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {provider} webhook: {e}")
            return None

        parser = get_parser(provider)
        event = parser.parse_webhook(payload)
        event.signature_verified = True

        # 3. Idempotency check
        dedup_key = f"{provider}:{event.remote_event_id or event.id}"
        if dedup_key in self._processed_events:
            logger.info(f"Duplicate webhook event skipped: {dedup_key}")
            return None
        self._processed_events[dedup_key] = True

        # Trim dedup dict to prevent unbounded memory growth (LRU eviction)
        if len(self._processed_events) > 10000:
            keys = list(self._processed_events.keys())
            for k in keys[:5000]:  # Remove oldest 5000 entries
                del self._processed_events[k]

        logger.info(
            f"Processed {provider} webhook: {event.event_type} "
            f"(candidate={event.candidate.remote_id if event.candidate else 'N/A'}, "
            f"application={event.application.remote_id if event.application else 'N/A'})"
        )

        return event

    async def should_auto_analyze(self, event: WebhookEvent) -> bool:
        """
        Determine if this webhook event should trigger automatic VetLayer analysis.

        Auto-trigger on:
          - New application (candidate applied to a job)
          - Candidate moved to screening/review stage
        """
        if event.event_type == WebhookEventType.APPLICATION_CREATED:
            return True

        if event.event_type == WebhookEventType.APPLICATION_STAGE_CHANGED:
            stage = (event.application.stage if event.application else "").lower()
            auto_trigger_stages = {
                "screen", "screening", "review", "application review",
                "recruiter screen", "initial review", "phone screen",
            }
            return any(s in stage for s in auto_trigger_stages)

        return False

    def get_signature_header_name(self, provider: ATSProvider) -> str:
        """Return the HTTP header name each ATS uses for webhook signatures."""
        return {
            ATSProvider.GREENHOUSE: "Signature",
            ATSProvider.LEVER: "X-Lever-Signature",
            ATSProvider.ASHBY: "X-Ashby-Signature",
            ATSProvider.WORKDAY: "",  # No webhooks
            ATSProvider.GENERIC: "X-Webhook-Signature",
        }.get(provider, "X-Webhook-Signature")
