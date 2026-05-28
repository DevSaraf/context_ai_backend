"""
KRAB — Platform OAuth Credential Provider (Path B — minimal)
=============================================================

Single source of truth for which OAuth app credentials are available on this
deployment.  Replaces connector_settings.py with a richer interface (typed
dataclass, configured_providers(), redirect_uri()) but reads the SAME env
vars so nothing in .env changes.

    NOTION_CLIENT_ID / NOTION_CLIENT_SECRET
    SLACK_CLIENT_ID  / SLACK_CLIENT_SECRET
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
    GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET
    CONFLUENCE_CLIENT_ID / CONFLUENCE_CLIENT_SECRET

Token encryption is still handled by crypto.py.
OAuth state CSRF is still handled by connector_router.py's existing nonce.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Platform OAuth app credentials
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OAuthAppCredentials:
    """Credentials for YOUR single registered app at a given provider."""
    provider: str
    client_id: str
    client_secret: str
    extra_authorize_params: Optional[Dict[str, str]] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


# Map connector_type -> env var prefix.  Uses the SAME prefixes as the
# existing connector_settings.py so no .env changes are required.
_PROVIDER_ENV_PREFIX: Dict[str, str] = {
    "notion": "NOTION",
    "slack": "SLACK",
    "google_drive": "GOOGLE",
    "github": "GITHUB",
    "confluence": "CONFLUENCE",
}

# Extra params appended to the authorize URL.  Google Drive already includes
# access_type=offline + prompt=consent inside its connector, so it is NOT
# listed here — that would cause duplication.
_PROVIDER_EXTRA_AUTHORIZE: Dict[str, Dict[str, str]] = {
    # "google_drive" intentionally omitted — handled by GoogleDriveConnector
}


class CredentialProvider:
    """Reads platform OAuth app credentials from the environment.

    Usage:
        creds = credential_provider.get("notion")
        if not creds.is_configured:
            ... return a clear 'not enabled' error
    """

    def __init__(self, env: Optional[Dict[str, str]] = None):
        self._env = env if env is not None else os.environ

    def get(self, connector_type: str) -> OAuthAppCredentials:
        prefix = _PROVIDER_ENV_PREFIX.get(connector_type)
        if prefix is None:
            logger.warning("No env prefix registered for connector_type=%s", connector_type)
            return OAuthAppCredentials(connector_type, "", "")

        client_id = self._env.get(f"{prefix}_CLIENT_ID", "").strip()
        client_secret = self._env.get(f"{prefix}_CLIENT_SECRET", "").strip()

        if not client_id or not client_secret:
            logger.info(
                "Connector '%s' not configured (missing %s_CLIENT_ID / _CLIENT_SECRET).",
                connector_type, prefix,
            )

        return OAuthAppCredentials(
            provider=connector_type,
            client_id=client_id,
            client_secret=client_secret,
            extra_authorize_params=_PROVIDER_EXTRA_AUTHORIZE.get(connector_type),
        )

    def configured_providers(self) -> List[str]:
        """Which connectors are actually usable on this deployment."""
        return [ct for ct in _PROVIDER_ENV_PREFIX if self.get(ct).is_configured]

    def redirect_uri(self, connector_type: str) -> str:
        """Build the redirect URI you paste into each provider's dev console."""
        base = self._env.get("KRAB_OAUTH_BASE_URL", "http://localhost:8000").rstrip("/")
        return f"{base}/connectors/oauth/callback"


# Module-level singleton (stateless beyond env — safe to share).
credential_provider = CredentialProvider()


# --------------------------------------------------------------------------- #
# CLI diagnostic — `python -m app.oauth_credentials`
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    cp = credential_provider
    print("Configured providers:", cp.configured_providers() or "(none)")
    for p in _PROVIDER_ENV_PREFIX:
        print(f"  - {p:14s} configured={cp.get(p).is_configured}  redirect={cp.redirect_uri(p)}")
