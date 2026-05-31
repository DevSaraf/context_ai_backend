"""
KRAB — Google Drive (drive.file + Picker model)
===============================================

WHAT CHANGED vs your drive.readonly connector
----------------------------------------------
1. SCOPE: now requests `drive.file` (non-sensitive, NO Google verification
   needed) instead of `drive.readonly` (restricted, needs a security audit).
   With drive.file, KRAB can only touch files the USER explicitly grants via
   the Google Picker — which is exactly the "let users choose" UX you want.

2. fetch_documents NO LONGER crawls the whole Drive. It fetches ONLY the file
   IDs the user has selected (stored on the integration). This is both the
   security model drive.file enforces AND the behavior you asked for.

3. New helper `fetch_selected(file_ids)` pulls a specific set of files. The
   sync path calls this with the saved selection.

The OAuth dance (get_oauth_url / exchange_code / refresh) is unchanged in shape
from your existing connector — only the scope string differs.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from .base_connector import BaseConnector, Document, register_connector

logger = logging.getLogger(__name__)

# Google Workspace MIME types we export as text, and what we export them to.
_EXPORT_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
# Plain files we download directly (no export needed).
_DIRECT_TEXT = {"text/plain", "text/markdown", "text/csv", "application/json"}


@register_connector
class GoogleDriveConnector(BaseConnector):
    connector_type = "google_drive"
    display_name = "Google Drive"
    icon = "google_drive"
    description = "Select documents from your Google Drive to import into KRAB"
    oauth_required = True

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    api_base = "https://www.googleapis.com/drive/v3"

    # drive.file = per-file access granted via Picker. NON-sensitive scope.
    # userinfo.email lets us label the connection. Keep scopes minimal.
    SCOPES = [
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/userinfo.email",
    ]

    def __init__(self, config=None, access_token=None, refresh_token=None,
                 client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        if not client_id or not client_secret:
            raise ValueError(
                "GoogleDriveConnector requires platform client_id/client_secret; "
                "they are injected by the framework."
            )
        self.client_id = client_id
        self.client_secret = client_secret

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    # ---- OAuth ------------------------------------------------------------ #
    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.SCOPES),     # Google wants space-separated
            "access_type": "offline",            # so we get a refresh_token
            "prompt": "consent",                 # force refresh_token on re-auth
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self.token_url, data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            data = resp.json()
            if "error" in data:
                raise Exception(f"Google OAuth error: {data.get('error_description', data['error'])}")

            expires_at = None
            if data.get("expires_in"):
                from datetime import timedelta, timezone
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))

            # Grab the email to label the connection.
            email = ""
            try:
                async with httpx.AsyncClient(timeout=10) as c2:
                    u = await c2.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {data['access_token']}"},
                    )
                    if u.status_code == 200:
                        email = u.json().get("email", "")
            except Exception:
                pass

            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": expires_at,
                "account_id": email,
                "account_name": email,
            }

    async def refresh_access_token(self) -> Dict[str, Any]:
        if not self.refresh_token:
            raise Exception("No refresh token available; user must reconnect.")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(self.token_url, data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            data = resp.json()
            if "error" in data:
                raise Exception(f"Google refresh error: {data.get('error')}")
            expires_at = None
            if data.get("expires_in"):
                from datetime import timedelta, timezone
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
            return {
                "access_token": data["access_token"],
                # Google omits refresh_token on refresh — keep the old one.
                "refresh_token": data.get("refresh_token", self.refresh_token),
                "expires_at": expires_at,
            }

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.api_base}/about",
                                        params={"fields": "user"},
                                        headers=self._headers())
                if resp.status_code == 200:
                    email = resp.json().get("user", {}).get("emailAddress", "Drive")
                    return True, f"Connected as {email}"
                return False, f"API error: {resp.status_code}"
        except Exception as e:
            return False, str(e)

    # ---- Selective fetch (the new model) --------------------------------- #
    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        """Sync entrypoint. With drive.file we fetch ONLY selected files.

        The selected IDs live on the integration; the sync layer passes them in
        through `self.config['selected_file_ids']` when it builds the connector.
        If nothing is selected yet, we return [] (nothing to sync) rather than
        trying — and failing — to crawl the whole Drive.
        """
        file_ids = (self.config or {}).get("selected_file_ids") or []
        if not file_ids:
            logger.info("Google Drive: no files selected yet; nothing to sync.")
            return []
        return await self.fetch_selected(file_ids, since=since)

    async def fetch_selected(self, file_ids: List[str],
                             since: Optional[datetime] = None) -> List[Document]:
        documents: List[Document] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for fid in file_ids:
                try:
                    meta = await self._get_metadata(client, fid)
                    if not meta:
                        continue
                    # Honor incremental sync: skip unchanged files.
                    modified = meta.get("modifiedTime", "")
                    if since and modified:
                        mdt = datetime.fromisoformat(modified.replace("Z", "+00:00"))
                        if since.tzinfo is None:
                            since = since.replace(tzinfo=timezone.utc)
                        if mdt < since:
                            continue
                    text = await self._download_text(client, meta)
                    if text and text.strip():
                        documents.append(Document(
                            external_id=fid,
                            title=meta.get("name", "Untitled"),
                            content=text[:50000],
                            source_url=meta.get("webViewLink", ""),
                            source_type="document",
                            metadata={"mime_type": meta.get("mimeType", "")},
                            updated_at=datetime.fromisoformat(modified.replace("Z", "+00:00")) if modified else None,
                        ))
                except Exception as e:
                    logger.warning("Google Drive: failed on file %s: %s", fid, e)
        logger.info("Google Drive: fetched %d selected files", len(documents))
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        docs = await self.fetch_selected([external_id])
        return docs[0] if docs else None

    # ---- internals -------------------------------------------------------- #
    async def _get_metadata(self, client: httpx.AsyncClient, fid: str) -> Optional[Dict]:
        resp = await client.get(
            f"{self.api_base}/files/{fid}",
            params={"fields": "id,name,mimeType,modifiedTime,webViewLink", "supportsAllDrives": "true"},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            logger.warning("Drive metadata %s -> %s", fid, resp.status_code)
            return None
        return resp.json()

    async def _download_text(self, client: httpx.AsyncClient, meta: Dict) -> str:
        fid = meta["id"]
        mime = meta.get("mimeType", "")
        logger.info("Drive _download_text: name=%s mime=%r", meta.get("name"), mime)
        # Google-native docs must be EXPORTED, not downloaded.
        if mime in _EXPORT_MAP:
            resp = await client.get(
                f"{self.api_base}/files/{fid}/export",
                params={"mimeType": _EXPORT_MAP[mime]},
                headers=self._headers(),
            )
            return resp.text if resp.status_code == 200 else ""
        # Plain text-ish files: direct media download.
        if mime in _DIRECT_TEXT:
            resp = await client.get(
                f"{self.api_base}/files/{fid}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers=self._headers(),
            )
            return resp.text if resp.status_code == 200 else ""
        # PDFs / docx: download bytes and let your existing extractor handle them.
        # (Hook into whatever you use for uploaded-file parsing.)
        if mime in ("application/pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
            resp = await client.get(
                f"{self.api_base}/files/{fid}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers=self._headers(),
            )
            logger.info("Drive media download: name=%s mime=%s status=%s bytes=%s",
                        meta.get("name"), mime, resp.status_code, len(resp.content))
            if resp.status_code == 200:
                return self._extract_bytes(resp.content, mime, meta.get("name", ""))
            return ""
        logger.info("Drive: skipping unsupported mime %s for %s", mime, meta.get("name"))
        return ""

    def _extract_bytes(self, data: bytes, mime: str, name: str) -> str:
        """Reuse your existing file-parsing pipeline."""
        try:
            if mime == "application/pdf":
                from app.file_parser import _parse_pdf
                parsed = _parse_pdf(data, name)
                text = parsed.text if parsed.is_valid else ""
                logger.info("PDF extract: name=%s bytes=%d chars=%d valid=%s", name, len(data), len(text), parsed.is_valid)
                return text
            if mime.endswith("wordprocessingml.document"):
                from app.file_parser import _parse_docx
                parsed = _parse_docx(data, name)
                text = parsed.text if parsed.is_valid else ""
                logger.info("DOCX extract: name=%s bytes=%d chars=%d valid=%s", name, len(data), len(text), parsed.is_valid)
                return text
        except Exception as e:
            logger.warning("Byte extraction failed for %s: %s", name, e)
        return ""
