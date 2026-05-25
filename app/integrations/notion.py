"""
KRAB — Notion Connector
OAuth2 integration to index Notion pages and databases.
"""

import httpx
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from urllib.parse import urlencode
import logging
import base64

from .base_connector import BaseConnector, Document, register_connector

logger = logging.getLogger(__name__)


@register_connector
class NotionConnector(BaseConnector):
    connector_type = "notion"
    display_name = "Notion"
    icon = "notion"
    description = "Index pages and databases from your Notion workspace"
    oauth_required = True

    auth_url = "https://api.notion.com/v1/oauth/authorize"
    token_url = "https://api.notion.com/v1/oauth/token"
    api_base = "https://api.notion.com/v1"
    api_version = "2022-06-28"

    def __init__(self, config=None, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Notion-Version": self.api_version,
            "Content-Type": "application/json",
        }

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "owner": "user",
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
            )
            data = resp.json()
            if "error" in data:
                raise Exception(f"Notion OAuth error: {data.get('error')}")

            return {
                "access_token": data["access_token"],
                "refresh_token": "",  # Notion doesn't use refresh tokens
                "expires_at": None,
                "workspace_name": data.get("workspace_name", ""),
                "workspace_id": data.get("workspace_id", ""),
            }

    async def refresh_access_token(self) -> Dict[str, Any]:
        # Notion access tokens don't expire, no refresh needed
        return {"access_token": self.access_token, "refresh_token": ""}

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.api_base}/users/me",
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    user = resp.json()
                    name = user.get("name", "Unknown")
                    return True, f"Connected as {name}"
                return False, f"API error: {resp.status_code}"
        except Exception as e:
            return False, str(e)

    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []

        async with httpx.AsyncClient(timeout=60) as client:
            # Search all accessible pages
            has_more = True
            start_cursor = None

            while has_more:
                body: Dict[str, Any] = {
                    "page_size": 100,
                    "filter": {"property": "object", "value": "page"},
                }
                if start_cursor:
                    body["start_cursor"] = start_cursor

                resp = await client.post(
                    f"{self.api_base}/search",
                    json=body,
                    headers=self._headers(),
                )

                if resp.status_code != 200:
                    logger.error(f"Notion search error: {resp.status_code}")
                    break

                data = resp.json()
                results = data.get("results", [])

                for page in results:
                    # Skip pages modified before `since`
                    last_edited = page.get("last_edited_time", "")
                    if since and last_edited:
                        edited_dt = datetime.fromisoformat(last_edited.replace("Z", "+00:00"))
                        if edited_dt < since:
                            continue

                    try:
                        content = await self._get_page_content(client, page["id"])
                        title = self._get_page_title(page)

                        if content and content.strip():
                            doc = Document(
                                external_id=page["id"],
                                title=title,
                                content=content,
                                source_url=page.get("url", ""),
                                source_type="wiki",
                                metadata={
                                    "last_edited_by": page.get("last_edited_by", {}).get("id", ""),
                                    "parent_type": page.get("parent", {}).get("type", ""),
                                },
                                updated_at=datetime.fromisoformat(last_edited.replace("Z", "+00:00")) if last_edited else None,
                            )
                            documents.append(doc)
                    except Exception as e:
                        logger.warning(f"Failed to process Notion page: {e}")

                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

                if len(documents) >= 500:
                    break

        logger.info(f"Notion: fetched {len(documents)} pages")
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.api_base}/pages/{external_id}",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                return None

            page = resp.json()
            content = await self._get_page_content(client, external_id)
            if not content:
                return None

            return Document(
                external_id=page["id"],
                title=self._get_page_title(page),
                content=content,
                source_url=page.get("url", ""),
                source_type="wiki",
            )

    async def _get_page_content(self, client: httpx.AsyncClient, page_id: str) -> str:
        """Extract text content from all blocks in a Notion page."""
        blocks = []
        has_more = True
        start_cursor = None

        while has_more:
            params = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor

            resp = await client.get(
                f"{self.api_base}/blocks/{page_id}/children",
                params=params,
                headers=self._headers(),
            )

            if resp.status_code != 200:
                break

            data = resp.json()
            blocks.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        # Extract text from blocks
        text_parts = []
        for block in blocks:
            text = self._extract_block_text(block)
            if text:
                text_parts.append(text)

        return "\n".join(text_parts)[:50000]

    def _extract_block_text(self, block: Dict) -> str:
        """Extract text from a Notion block."""
        block_type = block.get("type", "")
        block_data = block.get(block_type, {})

        if not block_data:
            return ""

        # Handle rich_text arrays
        rich_text = block_data.get("rich_text", [])
        if rich_text:
            text = " ".join([rt.get("plain_text", "") for rt in rich_text])

            # Add prefix for headings
            if block_type == "heading_1":
                return f"# {text}"
            elif block_type == "heading_2":
                return f"## {text}"
            elif block_type == "heading_3":
                return f"### {text}"
            elif block_type == "bulleted_list_item":
                return f"* {text}"
            elif block_type == "numbered_list_item":
                return f"- {text}"
            elif block_type == "to_do":
                checked = "[x]" if block_data.get("checked") else "[ ]"
                return f"{checked} {text}"
            elif block_type == "toggle":
                return f"> {text}"
            elif block_type == "quote":
                return f"> {text}"
            elif block_type == "callout":
                emoji = block_data.get("icon", {}).get("emoji", "")
                return f"{emoji} {text}"
            elif block_type == "code":
                lang = block_data.get("language", "")
                return f"```{lang}\n{text}\n```"
            return text

        # Handle other block types
        if block_type == "divider":
            return "---"
        elif block_type == "table_of_contents":
            return ""
        elif block_type == "breadcrumb":
            return ""

        return ""

    def _get_page_title(self, page: Dict) -> str:
        """Extract the title from a Notion page object."""
        properties = page.get("properties", {})

        # Look for "title" type property (could be named anything)
        for prop in properties.values():
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return " ".join([t.get("plain_text", "") for t in title_parts]) or "Untitled"

        return "Untitled"
