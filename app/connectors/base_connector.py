"""
connectors/base_connector.py
============================
The foundation every KRAB connector plugs into. This is the contract your
Notion and Slack connectors already import.

Design goal: adding a new source should mean implementing ~6 small,
provider-specific methods and NOTHING ELSE. A connector never touches the
database, never stores tokens, never chunks or embeds, never triggers
re-synthesis. The framework (sync_service + ingestion) owns all of that. That
separation is exactly what was missing before and what made integrations
painful.

A connector's whole job:
    1. Build an OAuth URL              -> get_oauth_url
    2. Exchange a code for tokens      -> exchange_code
    3. (optionally) refresh a token    -> refresh_access_token
    4. Say whether it's connected      -> test_connection
    5. Yield Documents since a time    -> fetch_documents
    6. (optionally) fetch one Document  -> fetch_document_by_id

Everything downstream consumes the uniform `Document` shape, so the rest of the
system never needs to know whether a doc came from Notion, Slack, or anywhere.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type


@dataclass
class Document:
    """The uniform unit every connector emits.

    Downstream (ingestion.py) converts this to `knowledge_chunks`, so a
    connector author only thinks in Documents -- never chunks or embeddings.
    """

    external_id: str                       # stable ID in the source system
    title: str
    content: str
    source_url: str = ""
    source_type: str = ""                  # connector's own subtype: "wiki", "conversation"...
    metadata: Dict[str, Any] = field(default_factory=dict)
    updated_at: Optional[datetime] = None

    def content_hash(self) -> str:
        """Stable hash of title+content, used for dedup / change detection."""
        h = hashlib.sha256()
        h.update((self.title or "").encode("utf-8"))
        h.update(b"\x00")
        h.update((self.content or "").encode("utf-8"))
        return h.hexdigest()[:32]

    def is_meaningful(self, min_chars: int = 30) -> bool:
        return bool(self.content) and len(self.content.strip()) >= min_chars


# --------------------------------------------------------------------------- #
# Registry: connectors self-register via @register_connector
# --------------------------------------------------------------------------- #
CONNECTOR_REGISTRY: Dict[str, Type["BaseConnector"]] = {}


def register_connector(cls: Type["BaseConnector"]) -> Type["BaseConnector"]:
    """Class decorator. Registers a connector by its `connector_type`.

    Lets the framework (and the frontend) discover connectors automatically --
    no central list to edit when you add one.
    """
    ctype = getattr(cls, "connector_type", "")
    if not ctype:
        raise ValueError(f"{cls.__name__} must set a non-empty connector_type")
    if ctype in CONNECTOR_REGISTRY and CONNECTOR_REGISTRY[ctype] is not cls:
        raise ValueError(f"Duplicate connector_type '{ctype}'")
    CONNECTOR_REGISTRY[ctype] = cls
    return cls


def get_connector_class(connector_type: str) -> Type["BaseConnector"]:
    if connector_type not in CONNECTOR_REGISTRY:
        raise KeyError(
            f"Unknown connector '{connector_type}'. "
            f"Known: {sorted(CONNECTOR_REGISTRY)}"
        )
    return CONNECTOR_REGISTRY[connector_type]


def available_connectors() -> List[Dict[str, Any]]:
    """Self-describing catalog for the UI. No hardcoded list to maintain."""
    return [cls.metadata() for cls in CONNECTOR_REGISTRY.values()]


# --------------------------------------------------------------------------- #
# The base class
# --------------------------------------------------------------------------- #
class BaseConnector(abc.ABC):
    # ---- declarative metadata (override in subclass) ---- #
    connector_type: str = ""        # "notion", "slack", ...
    display_name: str = ""          # "Notion"
    icon: str = ""                  # UI icon key
    description: str = ""           # one-liner for the UI
    oauth_required: bool = True
    scopes: List[str] = []

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        # `config` carries non-secret settings (channel filters, etc.) AND, at
        # runtime only, the OAuth client_id/secret injected by the framework
        # from env -- secrets are never persisted in config.
        self.config: Dict[str, Any] = config or {}
        self.access_token = access_token
        self.refresh_token = refresh_token

    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {
            "connector_type": cls.connector_type,
            "display_name": cls.display_name,
            "icon": cls.icon,
            "description": cls.description,
            "oauth_required": cls.oauth_required,
            "scopes": cls.scopes,
        }

    # ---- OAuth lifecycle ---- #
    @abc.abstractmethod
    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Return the provider authorize URL the user is sent to."""

    @abc.abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange an auth code for tokens.

        Must return a dict with at least:
            access_token, refresh_token (may be ""), expires_at (datetime|None),
            and optionally display_name (workspace/team) for the UI.
        """

    async def refresh_access_token(self) -> Dict[str, Any]:
        """Refresh an expiring token. Default: no-op (tokens don't expire).

        Override for providers that use refresh tokens. Return at least
        {access_token, refresh_token}.
        """
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token or "",
        }

    @abc.abstractmethod
    async def test_connection(self) -> Tuple[bool, str]:
        """Return (ok, human_message)."""

    # ---- data ---- #
    @abc.abstractmethod
    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        """Yield documents changed since `since` (None = full backfill).

        Implementations should honor `since` for incremental sync and cap the
        batch size to stay within memory/rate limits.
        """

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        """Fetch a single document. Optional; default returns None."""
        return None
