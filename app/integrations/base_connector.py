"""
KRAB — Base Connector & Registry
Abstract base class for all third-party connectors.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class Document:
    """Standardized document object returned by all connectors."""
    def __init__(
        self,
        external_id: str,
        title: str,
        content: str,
        source_url: str = "",
        source_type: str = "document",
        metadata: Dict[str, Any] = None,
        updated_at: Optional[datetime] = None,
        permissions: List[str] = None,
    ):
        self.external_id = external_id
        self.title = title
        self.content = content
        self.source_url = source_url
        self.source_type = source_type
        self.metadata = metadata or {}
        self.updated_at = updated_at
        self.permissions = permissions or []


class BaseConnector(ABC):
    """
    Abstract base class for all KRAB connectors.
    Each connector must implement these methods.
    """

    connector_type: str = ""
    display_name: str = ""
    icon: str = ""
    description: str = ""
    oauth_required: bool = True

    # OAuth URLs (override in subclass)
    auth_url: str = ""
    token_url: str = ""
    scopes: List[str] = []

    def __init__(self, config: Dict[str, Any] = None, access_token: str = None,
                 refresh_token: str = None, client_id: str = None, client_secret: str = None):
        self.config = config or {}
        self.access_token = access_token
        self.refresh_token = refresh_token
        # OAuth creds: prefer explicit params, then config dict, then empty
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")

    # ---- OAuth Flow ----

    @abstractmethod
    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Return the OAuth authorization URL for user to grant access."""
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange OAuth code for tokens. Returns {"access_token": ..., "refresh_token": ..., "expires_at": ...}."""
        pass

    @abstractmethod
    async def refresh_access_token(self) -> Dict[str, Any]:
        """Refresh the access token. Returns updated token dict."""
        pass

    # ---- Data Fetching ----

    @abstractmethod
    async def test_connection(self) -> Tuple[bool, str]:
        """Test if the connection is valid. Returns (success, message)."""
        pass

    @abstractmethod
    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        """
        Fetch documents from the source. 
        If `since` is provided, only fetch documents modified after that time (incremental sync).
        Returns a list of Document objects.
        """
        pass

    @abstractmethod
    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        """Fetch a single document by its external ID."""
        pass

    # ---- Helpers ----

    def get_connector_info(self) -> Dict[str, Any]:
        return {
            "type": self.connector_type,
            "name": self.display_name,
            "icon": self.icon,
            "description": self.description,
            "oauth_required": self.oauth_required,
        }


# ============================================================
# CONNECTOR REGISTRY
# ============================================================

_CONNECTOR_REGISTRY: Dict[str, type] = {}


def register_connector(cls):
    """Decorator to register a connector class."""
    _CONNECTOR_REGISTRY[cls.connector_type] = cls
    return cls


def get_connector_class(connector_type: str) -> Optional[type]:
    return _CONNECTOR_REGISTRY.get(connector_type)


def get_all_connector_types() -> List[Dict[str, Any]]:
    """Return info about all registered connector types."""
    return [cls().get_connector_info() for cls in _CONNECTOR_REGISTRY.values()]


def create_connector(connector_type: str, **kwargs) -> BaseConnector:
    """
    Factory: create a connector instance.
    Passes all kwargs through — including client_id, client_secret.
    """
    cls = get_connector_class(connector_type)
    if not cls:
        raise ValueError(f"Unknown connector type: {connector_type}")
    return cls(**kwargs)
