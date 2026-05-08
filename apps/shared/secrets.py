"""
Secrets provider: abstract access to secrets.
Default: EnvProvider (reads from environment; can be populated by .env, docker secrets, k8s, etc.).
No infra lock-in: swap provider via SECRETS_PROVIDER env or programmatic override.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class SecretsProvider(Protocol):
    """Protocol for secret lookup. Implement for Vault, AWS Secrets, etc."""

    def get(self, key: str, default: str = "") -> str:
        """Return secret value for key, or default if not found."""
        ...


class EnvSecretsProvider:
    """Read secrets from os.environ. Env can be populated by .env, docker, k8s, etc.
    Treats empty string as missing: returns default when value is absent or blank after strip.
    """

    def get(self, key: str, default: str = "") -> str:
        val = os.environ.get(key, default)
        if val is None:
            return default
        s = val.strip() if isinstance(val, str) else default
        return s if s else default


_provider: Optional[SecretsProvider] = None


def get_provider() -> SecretsProvider:
    """Return configured secrets provider. Default: EnvSecretsProvider."""
    global _provider
    if _provider is not None:
        return _provider
    kind = os.environ.get("SECRETS_PROVIDER", "env").strip().lower()
    if kind == "env":
        _provider = EnvSecretsProvider()
    else:
        _provider = EnvSecretsProvider()
    return _provider


def set_provider(provider: SecretsProvider) -> None:
    """Override secrets provider (e.g. for tests or Vault)."""
    global _provider
    _provider = provider


def get_secret(key: str, default: str = "") -> str:
    """Get secret value. Uses configured provider. No hardcoding."""
    return get_provider().get(key, default)
