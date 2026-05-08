"""Minimal tests for env parsing: empty -> default, invalid -> handled, valid -> parsed."""
import logging
import os

import pytest

from apps.shared.config import ConfigError
from apps.shared.env_helpers import parse_int, parse_int_default
from apps.shared.secrets import EnvSecretsProvider

try:
    from apps.api.core.settings import ApiSettings
    HAS_PYDANTIC_SETTINGS = True
except ImportError:
    HAS_PYDANTIC_SETTINGS = False


class TestEnvSecretsProvider:
    """get_secret / EnvSecretsProvider: empty string treated as missing -> default."""

    def test_missing_key_returns_default(self):
        provider = EnvSecretsProvider()
        assert provider.get("NONEXISTENT_KEY", "default") == "default"

    def test_empty_string_returns_default(self):
        provider = EnvSecretsProvider()
        os.environ["TEST_EMPTY_VAR"] = ""
        try:
            assert provider.get("TEST_EMPTY_VAR", "86400") == "86400"
        finally:
            os.environ.pop("TEST_EMPTY_VAR", None)

    def test_blank_string_returns_default(self):
        provider = EnvSecretsProvider()
        os.environ["TEST_BLANK_VAR"] = "   "
        try:
            assert provider.get("TEST_BLANK_VAR", "86400") == "86400"
        finally:
            os.environ.pop("TEST_BLANK_VAR", None)

    def test_valid_value_returned(self):
        provider = EnvSecretsProvider()
        os.environ["TEST_VALID_VAR"] = " 12345 "
        try:
            assert provider.get("TEST_VALID_VAR", "0") == "12345"
        finally:
            os.environ.pop("TEST_VALID_VAR", None)


class TestGetIntEnv:
    """get_int_env: tests for reading int from os.environ directly. Never crashes."""

    def test_missing_var_returns_default(self):
        """Missing env var -> default (no warning)"""
        # Ensure var is not set
        os.environ.pop("TEST_MISSING_VAR", None)
        assert get_int_env("TEST_MISSING_VAR", default=60) == 60

    def test_empty_string_logs_warning_returns_default(self, caplog):
        """Empty string -> log warning and return default"""
        os.environ["TEST_EMPTY_VAR"] = ""
        try:
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_EMPTY_VAR", default=86400)
                assert result == 86400
                assert "TEST_EMPTY_VAR" in caplog.text
                assert "empty or whitespace-only" in caplog.text
                assert "using default: 86400" in caplog.text
        finally:
            os.environ.pop("TEST_EMPTY_VAR", None)

    def test_whitespace_logs_warning_returns_default(self, caplog):
        """Whitespace-only -> log warning and return default"""
        os.environ["TEST_WHITESPACE_VAR"] = "   "
        try:
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_WHITESPACE_VAR", default=86400)
                assert result == 86400
                assert "TEST_WHITESPACE_VAR" in caplog.text
                assert "empty or whitespace-only" in caplog.text
                assert "using default: 86400" in caplog.text
        finally:
            os.environ.pop("TEST_WHITESPACE_VAR", None)

    def test_non_numeric_logs_warning_returns_default(self, caplog):
        """Non-numeric string -> log warning and return default (never crash)"""
        os.environ["TEST_INVALID_VAR"] = "abc"
        try:
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_INVALID_VAR", default=60)
                assert result == 60
                assert "Environment variable TEST_INVALID_VAR has invalid integer value" in caplog.text
                assert "using default: 60" in caplog.text
        finally:
            os.environ.pop("TEST_INVALID_VAR", None)

    def test_float_string_logs_warning_returns_default(self, caplog):
        """Float string -> log warning and return default (never crash)"""
        os.environ["TEST_FLOAT_VAR"] = "12.3"
        try:
            with caplog.at_level(logging.WARNING):
                result = get_int_env("TEST_FLOAT_VAR", default=60)
                assert result == 60
                assert "Environment variable TEST_FLOAT_VAR has invalid integer value" in caplog.text
        finally:
            os.environ.pop("TEST_FLOAT_VAR", None)

    def test_valid_int_parsed(self):
        """Valid int -> parsed value"""
        os.environ["TEST_VALID_VAR"] = "3600"
        try:
            assert get_int_env("TEST_VALID_VAR", default=86400) == 3600
        finally:
            os.environ.pop("TEST_VALID_VAR", None)

    def test_valid_int_with_whitespace_parsed(self):
        """Valid int with whitespace -> parsed value"""
        os.environ["TEST_WHITESPACE_INT"] = "  123  "
        try:
            assert get_int_env("TEST_WHITESPACE_INT", default=0) == 123
        finally:
            os.environ.pop("TEST_WHITESPACE_INT", None)

    def test_zero_allowed(self):
        """Zero is valid"""
        os.environ["TEST_ZERO_VAR"] = "0"
        try:
            assert get_int_env("TEST_ZERO_VAR", default=60) == 0
        finally:
            os.environ.pop("TEST_ZERO_VAR", None)

    def test_negative_allowed(self):
        """Negative values are valid"""
        os.environ["TEST_NEG_VAR"] = "-10"
        try:
            assert get_int_env("TEST_NEG_VAR", default=60) == -10
        finally:
            os.environ.pop("TEST_NEG_VAR", None)

    def test_never_raises_exception(self):
        """get_int_env never raises exceptions - production safe"""
        test_cases = [
            ("TEST_MISSING", None, 60),
            ("TEST_EMPTY", "", 60),
            ("TEST_WHITESPACE", "   ", 60),
            ("TEST_INVALID", "abc", 60),
            ("TEST_FLOAT", "12.3", 60),
            ("TEST_VALID", "100", 100),
        ]
        
        for var_name, var_value, expected in test_cases:
            if var_value is None:
                os.environ.pop(var_name, None)
            else:
                os.environ[var_name] = var_value
            try:
                result = get_int_env(var_name, default=60)
                assert result == expected, f"Failed for {var_name}={var_value}"
            except Exception as e:
                pytest.fail(f"get_int_env raised {type(e).__name__}: {e} for {var_name}={var_value}")
            finally:
                os.environ.pop(var_name, None)

    def test_warning_includes_variable_name_and_value(self, caplog):
        """Warnings include variable name and provided value for debugging"""
        # Test empty string
        os.environ["TEST_VAR_EMPTY"] = ""
        try:
            with caplog.at_level(logging.WARNING):
                get_int_env("TEST_VAR_EMPTY", default=100)
                assert "TEST_VAR_EMPTY" in caplog.text
                assert "''" in caplog.text or "empty" in caplog.text
        finally:
            os.environ.pop("TEST_VAR_EMPTY", None)
        
        # Test invalid value
        os.environ["TEST_VAR_INVALID"] = "xyz123"
        try:
            with caplog.at_level(logging.WARNING):
                get_int_env("TEST_VAR_INVALID", default=200)
                assert "TEST_VAR_INVALID" in caplog.text
                assert "'xyz123'" in caplog.text or "xyz123" in caplog.text
        finally:
            os.environ.pop("TEST_VAR_INVALID", None)


class TestParseInt:
    """parse_int: comprehensive tests for safe int parsing from env vars."""

    def test_missing_var_returns_default(self):
        """Missing/None -> default (no warning)"""
        assert parse_int("", default=60, name="TEST_VAR") == 60
        assert parse_int(None, default=60, name="TEST_VAR") == 60  # type: ignore

    def test_empty_string_returns_default(self):
        """Empty string -> default (no warning)"""
        assert parse_int("", default=86400, name="CACHE_TTL_SECONDS") == 86400
        assert parse_int("   ", default=86400, name="CACHE_TTL_SECONDS") == 86400
        assert parse_int("\t\n", default=86400, name="CACHE_TTL_SECONDS") == 86400

    def test_invalid_string_returns_default_with_warning(self, caplog):
        """Invalid string -> default + warning"""
        with caplog.at_level(logging.WARNING):
            result = parse_int("abc", default=60, name="API_RATE_LIMIT")
            assert result == 60
            assert "Invalid integer value" in caplog.text
            assert "API_RATE_LIMIT" in caplog.text

    def test_invalid_string_raises_when_requested(self):
        """Invalid string -> ConfigError when raise_on_invalid=True"""
        with pytest.raises(ConfigError, match="Invalid integer value"):
            parse_int("abc", default=60, name="API_RATE_LIMIT", raise_on_invalid=True)

    def test_valid_int_parsed(self):
        """Valid int -> parsed value"""
        assert parse_int("3600", default=86400, name="TTL") == 3600
        assert parse_int(" 123 ", default=0, name="COUNT") == 123
        assert parse_int("0", default=60, name="ZERO") == 0
        assert parse_int("-5", default=0, name="NEGATIVE") == -5

    def test_min_val_clamping(self, caplog):
        """Value below min_val -> clamped + warning"""
        with caplog.at_level(logging.WARNING):
            result = parse_int("0", default=60, min_val=1, name="MIN_TEST")
            assert result == 1
            assert "below minimum" in caplog.text

    def test_max_val_clamping(self, caplog):
        """Value above max_val -> clamped + warning"""
        with caplog.at_level(logging.WARNING):
            result = parse_int("999999", default=60, max_val=1000, name="MAX_TEST")
            assert result == 1000
            assert "above maximum" in caplog.text

    def test_range_clamping(self, caplog):
        """Value out of range -> clamped to range"""
        with caplog.at_level(logging.WARNING):
            result = parse_int("5000", default=100, min_val=1, max_val=1000, name="RANGE_TEST")
            assert result == 1000
            result2 = parse_int("-10", default=100, min_val=1, max_val=1000, name="RANGE_TEST2")
            assert result2 == 1

    def test_zero_allowed_when_min_val_none(self):
        """Zero is valid when min_val is None"""
        assert parse_int("0", default=60, name="ZERO_OK") == 0

    def test_negative_allowed_when_min_val_none(self):
        """Negative values are valid when min_val is None"""
        assert parse_int("-10", default=60, name="NEG_OK") == -10

    def test_raise_on_invalid_with_range(self):
        """raise_on_invalid=True raises ConfigError for out-of-range values"""
        with pytest.raises(ConfigError, match="below minimum"):
            parse_int("0", default=60, min_val=1, name="MIN_ERR", raise_on_invalid=True)
        with pytest.raises(ConfigError, match="above maximum"):
            parse_int("2000", default=60, max_val=1000, name="MAX_ERR", raise_on_invalid=True)

    def test_no_warning_for_valid_values(self, caplog):
        """Valid values don't log warnings"""
        with caplog.at_level(logging.WARNING):
            parse_int("100", default=60, min_val=1, max_val=1000, name="VALID")
            assert "Invalid integer value" not in caplog.text
            assert "below minimum" not in caplog.text
            assert "above maximum" not in caplog.text

    def test_name_optional(self):
        """name parameter is optional"""
        assert parse_int("100", default=60) == 100
        assert parse_int("abc", default=60) == 60  # Uses "env_var" as default name


class TestParseIntDefault:
    """parse_int_default: invalid -> default, valid -> parsed and clamped."""

    def test_empty_returns_default(self):
        assert parse_int_default("", 86400, 1, 604800) == 86400
        assert parse_int_default("  ", 86400, 1, 604800) == 86400

    def test_invalid_returns_default(self):
        assert parse_int_default("abc", 86400, 1, 604800) == 86400
        assert parse_int_default("12.3", 86400, 1, 604800) == 86400

    def test_valid_parsed(self):
        assert parse_int_default("3600", 86400, 1, 604800) == 3600
        assert parse_int_default("86400", 86400, 1, 604800) == 86400

    def test_clamped_to_range(self):
        assert parse_int_default("0", 86400, 1, 604800) == 86400  # 0 -> default
        assert parse_int_default("1", 86400, 1, 604800) == 1
        assert parse_int_default("999999", 86400, 1, 604800) == 604800


@pytest.mark.skipif(not HAS_PYDANTIC_SETTINGS, reason="pydantic-settings not installed")
class TestApiSettingsJwtExpiry:
    """ApiSettings.JWT_EXPIRY_SECONDS: empty -> 86400, invalid -> raise, valid -> int."""

    def test_empty_string_becomes_default(self):
        s = ApiSettings(
            DATABASE_URL="postgresql://u:p@h/d",
            REDIS_URL="redis://localhost/0",
            JWT_SECRET="",
            JWT_EXPIRY_SECONDS="",  # validator: empty -> 86400
            API_KEY="",
        )
        assert s.JWT_EXPIRY_SECONDS == 86400

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="JWT_EXPIRY_SECONDS must be an integer"):
            ApiSettings(
                DATABASE_URL="postgresql://u:p@h/d",
                REDIS_URL="redis://localhost/0",
                JWT_SECRET="",
                JWT_EXPIRY_SECONDS="notanint",
                API_KEY="",
            )

    def test_valid_int_parsed(self):
        s = ApiSettings(
            DATABASE_URL="postgresql://u:p@h/d",
            REDIS_URL="redis://localhost/0",
            JWT_SECRET="",
            JWT_EXPIRY_SECONDS=3600,
            API_KEY="",
        )
        assert s.JWT_EXPIRY_SECONDS == 3600
