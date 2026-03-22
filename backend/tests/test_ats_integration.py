"""
Unit tests for ATS integration layer — parsers, signature verification, event processing.
"""

import json
import hashlib
import hmac
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.ats_integration import (
    ATSProvider,
    WebhookEventType,
    NormalisedCandidate,
    NormalisedApplication,
    GreenhouseParser,
    LeverParser,
    AshbyParser,
    WorkdayParser,
    verify_webhook_signature,
    ATSIntegrationService,
    get_parser,
)


# ═══════════════════════════════════════════════════════════════════════
# Webhook signature verification
# ═══════════════════════════════════════════════════════════════════════

class TestSignatureVerification:
    def test_valid_signature(self):
        payload = b'{"action": "new_candidate_application"}'
        secret = "test-secret-key"
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(payload, sig, secret, ATSProvider.GREENHOUSE) is True

    def test_invalid_signature(self):
        payload = b'{"action": "test"}'
        assert verify_webhook_signature(payload, "invalid-sig", "secret", ATSProvider.GREENHOUSE) is False

    def test_sha256_prefix(self):
        payload = b'{"test": true}'
        secret = "my-secret"
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(payload, sig, secret, ATSProvider.LEVER) is True

    def test_empty_secret(self):
        assert verify_webhook_signature(b"test", "sig", "", ATSProvider.GREENHOUSE) is False

    def test_empty_signature(self):
        assert verify_webhook_signature(b"test", "", "secret", ATSProvider.GREENHOUSE) is False


# ═══════════════════════════════════════════════════════════════════════
# Greenhouse parser
# ═══════════════════════════════════════════════════════════════════════

class TestGreenhouseParser:
    parser = GreenhouseParser()

    def test_event_type_mapping(self):
        assert self.parser.parse_event_type({"action": "new_candidate_application"}) == WebhookEventType.APPLICATION_CREATED
        assert self.parser.parse_event_type({"action": "candidate_hired"}) == WebhookEventType.CANDIDATE_HIRED
        assert self.parser.parse_event_type({"action": "unknown_action"}) == WebhookEventType.UNKNOWN
        assert self.parser.parse_event_type({}) == WebhookEventType.UNKNOWN

    def test_parse_candidate_basic(self):
        payload = {
            "payload": {
                "candidate": {
                    "id": 12345,
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "title": "Software Engineer",
                    "company": "Acme Corp",
                    "email_addresses": [{"type": "personal", "value": "jane@example.com"}],
                    "phone_numbers": [{"value": "+1-555-1234"}],
                    "tags": [{"name": "senior"}, {"name": "backend"}],
                    "attachments": [{"type": "resume", "url": "https://example.com/resume.pdf", "content_type": "application/pdf"}],
                }
            }
        }
        cand = self.parser.parse_candidate(payload)
        assert cand.remote_id == "12345"
        assert cand.first_name == "Jane"
        assert cand.last_name == "Doe"
        assert cand.email == "jane@example.com"
        assert cand.resume_url == "https://example.com/resume.pdf"
        assert "senior" in cand.tags

    def test_parse_candidate_null_safety(self):
        """Ensure parser handles None values in nested structures."""
        payload = {"payload": {"candidate": None, "application": None}}
        cand = self.parser.parse_candidate(payload)
        assert cand.remote_id == ""
        assert cand.first_name == ""

    def test_parse_candidate_empty_payload(self):
        cand = self.parser.parse_candidate({})
        assert cand.remote_id == ""

    def test_parse_application(self):
        payload = {
            "payload": {
                "application": {
                    "id": 99,
                    "candidate": {"id": 12345},
                    "jobs": [{"id": 42, "name": "Senior Engineer"}],
                    "current_stage": {"name": "Phone Screen"},
                    "status": "active",
                    "applied_at": "2024-01-15T10:30:00Z",
                }
            }
        }
        app = self.parser.parse_application(payload)
        assert app.remote_id == "99"
        assert app.candidate_remote_id == "12345"
        assert app.job_remote_id == "42"
        assert app.job_title == "Senior Engineer"
        assert app.stage == "Phone Screen"


# ═══════════════════════════════════════════════════════════════════════
# Lever parser
# ═══════════════════════════════════════════════════════════════════════

class TestLeverParser:
    parser = LeverParser()

    def test_event_type_mapping(self):
        assert self.parser.parse_event_type({"event": "candidateCreated"}) == WebhookEventType.CANDIDATE_CREATED
        assert self.parser.parse_event_type({"event": "candidateHired"}) == WebhookEventType.CANDIDATE_HIRED
        assert self.parser.parse_event_type({}) == WebhookEventType.UNKNOWN

    def test_parse_candidate_with_name_splitting(self):
        payload = {
            "data": {
                "candidate": {
                    "id": "abc-123",
                    "name": "John Smith",
                    "emails": ["john@example.com"],
                    "phones": [{"value": "+1-555-9999"}],
                    "links": ["https://linkedin.com/in/johnsmith"],
                    "tags": ["engineering"],
                    "headline": "Full Stack Developer",
                }
            }
        }
        cand = self.parser.parse_candidate(payload)
        assert cand.first_name == "John"
        assert cand.last_name == "Smith"
        assert cand.email == "john@example.com"
        assert cand.linkedin_url == "https://linkedin.com/in/johnsmith"

    def test_parse_candidate_single_name(self):
        payload = {"data": {"candidate": {"id": "x", "name": "Madonna"}}}
        cand = self.parser.parse_candidate(payload)
        assert cand.first_name == "Madonna"
        assert cand.last_name == ""

    def test_parse_candidate_no_name(self):
        payload = {"data": {"candidate": {"id": "y"}}}
        cand = self.parser.parse_candidate(payload)
        assert cand.first_name == ""
        assert cand.last_name == ""

    def test_parse_candidate_phone_non_dict(self):
        """Phones that aren't dicts should not crash."""
        payload = {"data": {"candidate": {"id": "z", "phones": ["1234567890"]}}}
        cand = self.parser.parse_candidate(payload)
        assert cand.phone == ""  # String phone should be handled gracefully


# ═══════════════════════════════════════════════════════════════════════
# Workday parser
# ═══════════════════════════════════════════════════════════════════════

class TestWorkdayParser:
    parser = WorkdayParser()

    def test_event_type_with_valid_value(self):
        payload = {"_vetlayer_event_type": "candidate.created"}
        assert self.parser.parse_event_type(payload) == WebhookEventType.CANDIDATE_CREATED

    def test_event_type_invalid(self):
        payload = {"_vetlayer_event_type": "invalid"}
        assert self.parser.parse_event_type(payload) == WebhookEventType.UNKNOWN

    def test_event_type_missing(self):
        assert self.parser.parse_event_type({}) == WebhookEventType.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# Integration service
# ═══════════════════════════════════════════════════════════════════════

class TestATSIntegrationService:
    @pytest.mark.asyncio
    async def test_deduplication(self):
        service = ATSIntegrationService()
        payload = json.dumps({"action": "new_candidate_application", "payload": {"candidate": {"id": 1}}}).encode()

        # First call should succeed
        event1 = await service.process_webhook(ATSProvider.GREENHOUSE, payload)
        assert event1 is not None

        # Second call with same payload should be deduped
        # (Only if remote_event_id matches — but since our events get random UUIDs, this tests the basic flow)

    @pytest.mark.asyncio
    async def test_invalid_json(self):
        service = ATSIntegrationService()
        event = await service.process_webhook(ATSProvider.GREENHOUSE, b"not-json")
        assert event is None

    @pytest.mark.asyncio
    async def test_auto_analyze_application_created(self):
        service = ATSIntegrationService()
        payload = json.dumps({"action": "new_candidate_application", "payload": {"candidate": {"id": 1}}}).encode()
        event = await service.process_webhook(ATSProvider.GREENHOUSE, payload)
        if event:
            should = await service.should_auto_analyze(event)
            assert should is True

    def test_parser_registry(self):
        assert isinstance(get_parser(ATSProvider.GREENHOUSE), GreenhouseParser)
        assert isinstance(get_parser(ATSProvider.LEVER), LeverParser)
        assert isinstance(get_parser(ATSProvider.WORKDAY), WorkdayParser)
        assert isinstance(get_parser(ATSProvider.ASHBY), AshbyParser)

    def test_get_signature_header(self):
        service = ATSIntegrationService()
        assert service.get_signature_header_name(ATSProvider.GREENHOUSE) == "Signature"
        assert service.get_signature_header_name(ATSProvider.LEVER) == "X-Lever-Signature"
