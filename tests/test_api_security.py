"""Tests for API security: auth, rate limit, CORS."""
from unittest.mock import MagicMock, patch

import pytest

try:
    from fastapi import Request
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def test_auth_required_when_disabled():
    """auth_required() is False when neither JWT_SECRET nor API_KEY set."""
    from apps.api import auth

    with patch.object(auth, "JWT_SECRET", ""), patch.object(auth, "API_KEY", ""):
        assert auth.auth_required() is False


def test_auth_required_when_api_key_set():
    """auth_required() is True when API_KEY set."""
    from apps.api import auth

    with patch.object(auth, "API_KEY", "secret"), patch.object(auth, "JWT_SECRET", ""):
        assert auth.auth_required() is True


def test_verify_api_key():
    """_verify_api_key accepts correct key."""
    from apps.api import auth

    with patch.object(auth, "API_KEY", "my-key"), patch.object(auth, "JWT_SECRET", ""):
        assert auth._verify_api_key("my-key") is True
        assert auth._verify_api_key("wrong") is False
        assert auth._verify_api_key(None) is False


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
def test_rate_limit_client_identifier():
    """_client_identifier uses IP or token hash."""
    from apps.api.middleware import _client_identifier

    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "192.168.1.1"
    assert "ip:" in _client_identifier(req)
    req.headers["X-API-Key"] = "abc"
    assert "token:" in _client_identifier(req)
