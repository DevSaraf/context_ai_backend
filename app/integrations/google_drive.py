"""
KRAB — Google Drive Connector
OAuth2 integration to index Google Drive documents.
"""

import httpx
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from urllib.parse import urlencode
import logging

from .base_connector import BaseConnector, Document, register_connector

logger = logging.getLogger(__name__)


@register_connector
class GoogleDriveConnector(BaseConnector):
    connector_type = "google_drive"
    display_name = "Google Drive"
    icon = "google-drive"
    description = "Index documents, spreadsheets, and presentations from Google Drive"
    oauth_required = True

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    ]

    # Supported MIME types and their export formats
    EXPORT_MIMES = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    SUPPORTED_MIMES = [
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ] + list(EXPORT_MIMES.keys())

    def __init__(self, config=None, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            data = resp.json()
            if "error" in data:
                raise Exception(f"OAuth error: {data['error_description']}")

            expires_in = data.get("expires_in", 3600)
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": datetime.utcnow().timestamp() + expires_in,
            }

    async def refresh_access_token(self) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            })
            data = resp.json()
            if "error" in data:
                raise Exception(f"Token refresh error: {data['error_description']}")

            expires_in = data.get("expires_in", 3600)
            return {
                "access_token": data["access_token"],
                "refresh_token": self.refresh_token,
                "expires_at": datetime.utcnow().timestamp() + expires_in,
            }

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/about",
                    params={"fields": "user"},
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                if resp.status_code == 200:
                    user = resp.json().get("user", {})
                    email = user.get("emailAddress", "Unknown")
                    return True, f"Connected as {email}"
                return False, f"API error: {resp.status_code}"
        except Exception as e:
            return False, str(e)

    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []
        page_token = None

        # Build query
        mime_filter = " or ".join([f"mimeType='{m}'" for m in self.SUPPORTED_MIMES])
        query = f"({mime_filter}) and trashed=false"
        if since:
            query += f" and modifiedTime > '{since.isoformat()}Z'"

        # Folder filter if configured
        folder_id = self.config.get("folder_id")
        if folder_id:
            query += f" and '{folder_id}' in parents"

        async with httpx.AsyncClient(timeout=60) as client:
            while True:
                params = {
                    "q": query,
                    "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,owners,size)",
                    "pageSize": 100,
                    "orderBy": "modifiedTime desc",
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    params=params,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )

                if resp.status_code != 200:
                    logger.error(f"Drive API error: {resp.status_code} {resp.text}")
                    break

                data = resp.json()
                files = data.get("files", [])

                for file in files:
                    try:
                        content = await self._get_file_content(client, file)
                        if content and content.strip():
                            doc = Document(
                                external_id=file["id"],
                                title=file.get("name", "Untitled"),
                                content=content,
                                source_url=file.get("webViewLink", ""),
                                source_type=self._classify_type(file.get("mimeType", "")),
                                metadata={
                                    "mime_type": file.get("mimeType"),
                                    "owners": [o.get("emailAddress", "") for o in file.get("owners", [])],
                                    "size": file.get("size"),
                                },
                                updated_at=datetime.fromisoformat(file["modifiedTime"].replace("Z", "+00:00")) if file.get("modifiedTime") else None,
                            )
                            documents.append(doc)
                    except Exception as e:
                        logger.warning(f"Failed to process file {file.get('name')}: {e}")

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                # Safety limit
                if len(documents) >= 1000:
                    logger.info("Reached 1000 document limit for sync")
                    break

        logger.info(f"Google Drive: fetched {len(documents)} documents")
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{external_id}",
                params={"fields": "id,name,mimeType,modifiedTime,webViewLink,owners"},
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            if resp.status_code != 200:
                return None

            file = resp.json()
            content = await self._get_file_content(client, file)
            if not content:
                return None

            return Document(
                external_id=file["id"],
                title=file.get("name", "Untitled"),
                content=content,
                source_url=file.get("webViewLink", ""),
                source_type=self._classify_type(file.get("mimeType", "")),
                updated_at=datetime.fromisoformat(file["modifiedTime"].replace("Z", "+00:00")) if file.get("modifiedTime") else None,
            )

    async def _get_file_content(self, client: httpx.AsyncClient, file: Dict) -> Optional[str]:
        """Download or export file content as text."""
        file_id = file["id"]
        mime_type = file.get("mimeType", "")

        try:
            if mime_type in self.EXPORT_MIMES:
                # Google native format — export
                export_mime = self.EXPORT_MIMES[mime_type]
                resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                    params={"mimeType": export_mime},
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
            else:
                # Binary file — download
                resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}",
                    params={"alt": "media"},
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )

            if resp.status_code != 200:
                return None

            # For text-based files, return content directly
            if mime_type in self.EXPORT_MIMES or mime_type in ("text/plain", "text/markdown", "text/csv"):
                return resp.text[:50000]  # Limit to 50k chars per doc

            # For binary (PDF, DOCX) — would need parsing library
            # For now, skip binary files or use a parser
            return None

        except Exception as e:
            logger.warning(f"Failed to get content for {file.get('name')}: {e}")
            return None

    def _classify_type(self, mime_type: str) -> str:
        type_map = {
            "application/vnd.google-apps.document": "document",
            "application/vnd.google-apps.spreadsheet": "spreadsheet",
            "application/vnd.google-apps.presentation": "presentation",
            "application/pdf": "document",
            "text/plain": "document",
            "text/markdown": "document",
            "text/csv": "spreadsheet",
        }
        return type_map.get(mime_type, "document")
