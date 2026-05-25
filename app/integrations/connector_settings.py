"""
KRAB — Connector OAuth Settings
Loads OAuth client_id / client_secret from environment variables.

Required .env variables:
  GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
  GOOGLE_CLIENT_SECRET=GOCSPX-xxx
  NOTION_CLIENT_ID=xxx
  NOTION_CLIENT_SECRET=secret_xxx
  GITHUB_CLIENT_ID=Ov23lixxx
  GITHUB_CLIENT_SECRET=xxx
  SLACK_CLIENT_ID=xxx
  SLACK_CLIENT_SECRET=xxx
  CONFLUENCE_CLIENT_ID=xxx
  CONFLUENCE_CLIENT_SECRET=xxx
"""

import os

# Map connector type to env var prefix
_ENV_PREFIX = {
    "google_drive": "GOOGLE",
    "notion": "NOTION",
    "github": "GITHUB",
    "slack": "SLACK",
    "confluence": "CONFLUENCE",
}


def get_oauth_creds(connector_type: str) -> dict:
    """Get OAuth credentials for a connector type (reads env vars at call time)."""
    prefix = _ENV_PREFIX.get(connector_type)
    if not prefix:
        return {"client_id": "", "client_secret": ""}
    return {
        "client_id": os.getenv(f"{prefix}_CLIENT_ID", ""),
        "client_secret": os.getenv(f"{prefix}_CLIENT_SECRET", ""),
    }
