"""
KRAB — Connector Registry
Import all connectors to register them.
"""

from .base_connector import (
    BaseConnector,
    Document,
    get_connector_class,
    get_all_connector_types,
    create_connector,
)

# Import to trigger @register_connector decorators
from .google_drive import GoogleDriveConnector
from .notion import NotionConnector
from .slack import SlackConnector
from .github import GitHubConnector, ConfluenceConnector

# Keep existing zendesk for backwards compatibility
from .zendesk import ZendeskClient

__all__ = [
    "BaseConnector",
    "Document",
    "get_connector_class",
    "get_all_connector_types",
    "create_connector",
    "GoogleDriveConnector",
    "NotionConnector",
    "SlackConnector",
    "GitHubConnector",
    "ConfluenceConnector",
    "ZendeskClient",
]
