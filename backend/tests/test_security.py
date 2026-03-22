"""
Unit tests for security utilities — password validation, hashing, JWT tokens.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.security import (
    hash_password,
    verify_password,
    validate_password_strength,
    create_access_token,
    create_refresh_token,
    decode_token,
)


class TestPasswordHashing:
    def test_hash_and_verify(self):
        password = "SecureP@ss123"
        hashed = hash_password(password)
        assert hashed != password  # Should be hashed
        assert verify_password(password, hashed) is True

    def test_wrong_password(self):
        hashed = hash_password("CorrectPassword1!")
        assert verify_password("WrongPassword1!", hashed) is False

    def test_different_hashes(self):
        """Same password should produce different hashes (bcrypt salting)."""
        h1 = hash_password("TestP@ss123")
        h2 = hash_password("TestP@ss123")
        assert h1 != h2  # Different salts


class TestPasswordStrength:
    def test_too_short(self):
        err = validate_password_strength("Ab1!")
        assert err is not None
        assert "8 characters" in err

    def test_no_uppercase(self):
        err = validate_password_strength("abcdefg1!")
        assert err is not None
        assert "uppercase" in err

    def test_no_lowercase(self):
        err = validate_password_strength("ABCDEFG1!")
        assert err is not None
        assert "lowercase" in err

    def test_no_digit(self):
        err = validate_password_strength("Abcdefgh!")
        assert err is not None
        assert "number" in err

    def test_no_special(self):
        err = validate_password_strength("Abcdefg1")
        assert err is not None
        assert "special" in err

    def test_strong_password(self):
        err = validate_password_strength("StrongP@ss1")
        assert err is None  # Should pass


class TestJWT:
    def test_access_token_roundtrip(self):
        token = create_access_token("user-123", "admin", "super_admin")
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["username"] == "admin"
        assert payload["role"] == "super_admin"
        assert payload["type"] == "access"

    def test_refresh_token(self):
        token = create_refresh_token("user-456")
        payload = decode_token(token)
        assert payload["sub"] == "user-456"
        assert payload["type"] == "refresh"

    def test_access_token_with_company(self):
        token = create_access_token("user-789", "recruiter", "company_admin", "company-abc")
        payload = decode_token(token)
        assert payload["company_id"] == "company-abc"
