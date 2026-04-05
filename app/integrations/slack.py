"""
KRAB — Slack Connector
OAuth2 integration to index Slack messages from channels.
"""

import httpx
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from urllib.parse import urlencode
import logging

from .base_connector import BaseConnector, Document, register_connector

logger = logging.getLogger(__name__)


@register_connector
class SlackConnector(BaseConnector):
    connector_type = "slack"
    display_name = "Slack"
    icon = "slack"
    description = "Index messages and threads from Slack channels"
    oauth_required = True

    auth_url = "https://slack.com/oauth/v2/authorize"
    token_url = "https://slack.com/api/oauth.v2.access"
    api_base = "https://slack.com/api"
    scopes = [
        "channels:read",
        "channels:history",
        "groups:read",
        "groups:history",
        "users:read",
    ]

    def __init__(self, config=None, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(self.scopes),
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
            })
            data = resp.json()
            if not data.get("ok"):
                raise Exception(f"Slack OAuth error: {data.get('error')}")

            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": None,
                "team_name": data.get("team", {}).get("name", ""),
                "team_id": data.get("team", {}).get("id", ""),
            }

    async def refresh_access_token(self) -> Dict[str, Any]:
        if not self.refresh_token:
            return {"access_token": self.access_token}

        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            })
            data = resp.json()
            if not data.get("ok"):
                raise Exception(f"Token refresh error: {data.get('error')}")

            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", self.refresh_token),
            }

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.api_base}/auth.test",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    team = data.get("team", "Unknown")
                    return True, f"Connected to {team}"
                return False, data.get("error", "Connection failed")
        except Exception as e:
            return False, str(e)

    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []

        async with httpx.AsyncClient(timeout=60) as client:
            # Get user map for display names
            user_map = await self._get_user_map(client)

            # Get channels
            channels = await self._get_channels(client)

            # Configured channels to index (or all if not specified)
            channel_filter = self.config.get("channels", [])

            for channel in channels:
                channel_name = channel.get("name", "")
                channel_id = channel.get("id", "")

                if channel_filter and channel_name not in channel_filter and channel_id not in channel_filter:
                    continue

                try:
                    messages = await self._get_channel_messages(client, channel_id, since)

                    # Group messages into conversation threads
                    threads = self._group_into_threads(messages)

                    for thread_ts, thread_msgs in threads.items():
                        content = self._format_thread(thread_msgs, user_map, channel_name)
                        if content and len(content.strip()) > 50:  # Skip very short messages
                            doc = Document(
                                external_id=f"{channel_id}:{thread_ts}",
                                title=f"#{channel_name} - {content[:80]}...",
                                content=content,
                                source_url=f"https://slack.com/archives/{channel_id}/p{thread_ts.replace('.', '')}",
                                source_type="conversation",
                                metadata={
                                    "channel": channel_name,
                                    "channel_id": channel_id,
                                    "message_count": len(thread_msgs),
                                },
                                updated_at=datetime.fromtimestamp(float(thread_ts)),
                            )
                            documents.append(doc)
                except Exception as e:
                    logger.warning(f"Failed to fetch messages from #{channel_name}: {e}")

                if len(documents) >= 500:
                    break

        logger.info(f"Slack: fetched {len(documents)} conversation threads")
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        # external_id format: "channel_id:thread_ts"
        parts = external_id.split(":", 1)
        if len(parts) != 2:
            return None
        # Would need to fetch specific thread - simplified for now
        return None

    async def _get_channels(self, client: httpx.AsyncClient) -> List[Dict]:
        channels = []
        cursor = None

        while True:
            params = {"limit": 200, "types": "public_channel,private_channel"}
            if cursor:
                params["cursor"] = cursor

            resp = await client.get(
                f"{self.api_base}/conversations.list",
                params=params,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            data = resp.json()
            if not data.get("ok"):
                break

            channels.extend(data.get("channels", []))
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return channels

    async def _get_channel_messages(self, client: httpx.AsyncClient, channel_id: str, since: Optional[datetime] = None) -> List[Dict]:
        messages = []
        params = {"channel": channel_id, "limit": 200}
        if since:
            params["oldest"] = str(since.timestamp())

        resp = await client.get(
            f"{self.api_base}/conversations.history",
            params=params,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        data = resp.json()
        if data.get("ok"):
            messages.extend(data.get("messages", []))

        return messages

    async def _get_user_map(self, client: httpx.AsyncClient) -> Dict[str, str]:
        user_map = {}
        resp = await client.get(
            f"{self.api_base}/users.list",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        data = resp.json()
        if data.get("ok"):
            for user in data.get("members", []):
                uid = user.get("id", "")
                name = user.get("real_name") or user.get("name") or uid
                user_map[uid] = name
        return user_map

    def _group_into_threads(self, messages: List[Dict]) -> Dict[str, List[Dict]]:
        """Group messages by thread_ts (replies go under their parent)."""
        threads = {}
        for msg in messages:
            ts = msg.get("thread_ts") or msg.get("ts", "")
            if ts not in threads:
                threads[ts] = []
            threads[ts].append(msg)

        # Sort messages within each thread
        for ts in threads:
            threads[ts].sort(key=lambda m: float(m.get("ts", "0")))

        return threads

    def _format_thread(self, messages: List[Dict], user_map: Dict[str, str], channel: str) -> str:
        """Format a thread of messages into readable text."""
        parts = []
        for msg in messages:
            user = user_map.get(msg.get("user", ""), "Unknown")
            text = msg.get("text", "")
            if text:
                parts.append(f"{user}: {text}")

        return "\n".join(parts)[:10000]
