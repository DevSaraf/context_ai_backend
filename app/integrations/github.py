"""
KRAB — GitHub Connector
Index repos, READMEs, issues, wikis, and PRs.
"""

import httpx
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from urllib.parse import urlencode
import base64
import logging

from .base_connector import BaseConnector, Document, register_connector

logger = logging.getLogger(__name__)


@register_connector
class GitHubConnector(BaseConnector):
    connector_type = "github"
    display_name = "GitHub"
    icon = "github"
    description = "Index repositories, issues, PRs, and documentation from GitHub"
    oauth_required = True

    auth_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"
    api_base = "https://api.github.com"
    scopes = ["repo", "read:org"]

    def __init__(self, config=None, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.token_url,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()
            if "error" in data:
                raise Exception(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": None,
            }

    async def refresh_access_token(self) -> Dict[str, Any]:
        return {"access_token": self.access_token}

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.api_base}/user", headers=self._headers())
                if resp.status_code == 200:
                    login = resp.json().get("login", "Unknown")
                    return True, f"Connected as @{login}"
                return False, f"API error: {resp.status_code}"
        except Exception as e:
            return False, str(e)

    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []

        async with httpx.AsyncClient(timeout=60) as client:
            # Get user's repos (or org repos if configured)
            repos = await self._get_repos(client)
            repo_filter = self.config.get("repos", [])

            for repo in repos:
                repo_name = repo["full_name"]
                if repo_filter and repo_name not in repo_filter:
                    continue

                # 1. Index README
                try:
                    readme = await self._get_readme(client, repo_name)
                    if readme:
                        documents.append(readme)
                except Exception as e:
                    logger.warning(f"README fetch failed for {repo_name}: {e}")

                # 2. Index recent issues
                try:
                    issues = await self._get_issues(client, repo_name, since)
                    documents.extend(issues)
                except Exception as e:
                    logger.warning(f"Issues fetch failed for {repo_name}: {e}")

                # 3. Index docs/ folder if exists
                try:
                    docs = await self._get_docs_folder(client, repo_name)
                    documents.extend(docs)
                except Exception:
                    pass  # Many repos don't have docs/

                if len(documents) >= 500:
                    break

        logger.info(f"GitHub: fetched {len(documents)} documents")
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        return None

    async def _get_repos(self, client: httpx.AsyncClient) -> List[Dict]:
        repos = []
        page = 1
        while True:
            resp = await client.get(
                f"{self.api_base}/user/repos",
                params={"page": page, "per_page": 100, "sort": "updated"},
                headers=self._headers(),
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            repos.extend(batch)
            page += 1
            if len(repos) >= 200:
                break
        return repos

    async def _get_readme(self, client: httpx.AsyncClient, repo_name: str) -> Optional[Document]:
        resp = await client.get(
            f"{self.api_base}/repos/{repo_name}/readme",
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")
        if not content.strip():
            return None

        return Document(
            external_id=f"github:{repo_name}:readme",
            title=f"{repo_name} - README",
            content=content[:30000],
            source_url=data.get("html_url", ""),
            source_type="document",
            metadata={"repo": repo_name, "type": "readme"},
        )

    async def _get_issues(self, client: httpx.AsyncClient, repo_name: str, since: Optional[datetime]) -> List[Document]:
        docs = []
        params = {"state": "all", "per_page": 50, "sort": "updated"}
        if since:
            params["since"] = since.isoformat() + "Z"

        resp = await client.get(
            f"{self.api_base}/repos/{repo_name}/issues",
            params=params,
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return []

        for issue in resp.json():
            if issue.get("pull_request"):
                continue  # Skip PRs from issues endpoint

            body = issue.get("body", "") or ""
            title = issue.get("title", "")
            labels = [lbl["name"] for lbl in issue.get("labels", [])]
            state = issue.get("state", "open")

            content = f"Issue: {title}\nState: {state}\nLabels: {', '.join(labels)}\n\n{body}"

            docs.append(Document(
                external_id=f"github:{repo_name}:issue:{issue['number']}",
                title=f"{repo_name} #{issue['number']}: {title}",
                content=content[:10000],
                source_url=issue.get("html_url", ""),
                source_type="issue",
                metadata={
                    "repo": repo_name,
                    "type": "issue",
                    "number": issue["number"],
                    "state": state,
                    "labels": labels,
                },
                updated_at=datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00")) if issue.get("updated_at") else None,
            ))

        return docs

    async def _get_docs_folder(self, client: httpx.AsyncClient, repo_name: str) -> List[Document]:
        """Try to index a docs/ folder if it exists."""
        docs = []
        resp = await client.get(
            f"{self.api_base}/repos/{repo_name}/contents/docs",
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return []

        for item in resp.json():
            if item["type"] == "file" and item["name"].endswith((".md", ".txt", ".rst")):
                file_resp = await client.get(item["url"], headers=self._headers())
                if file_resp.status_code == 200:
                    content = base64.b64decode(file_resp.json().get("content", "")).decode("utf-8", errors="ignore")
                    if content.strip():
                        docs.append(Document(
                            external_id=f"github:{repo_name}:doc:{item['path']}",
                            title=f"{repo_name} - {item['name']}",
                            content=content[:30000],
                            source_url=item.get("html_url", ""),
                            source_type="document",
                            metadata={"repo": repo_name, "type": "doc", "path": item["path"]},
                        ))
        return docs


# ============================================================
# CONFLUENCE CONNECTOR
# ============================================================

@register_connector
class ConfluenceConnector(BaseConnector):
    connector_type = "confluence"
    display_name = "Confluence"
    icon = "confluence"
    description = "Index pages and spaces from Atlassian Confluence"
    oauth_required = True

    auth_url = "https://auth.atlassian.com/authorize"
    token_url = "https://auth.atlassian.com/oauth/token"
    scopes = ["read:confluence-content.all", "read:confluence-space.summary", "offline_access"]

    def __init__(self, config=None, access_token=None, refresh_token=None, client_id=None, client_secret=None):
        super().__init__(config, access_token, refresh_token)
        self.client_id = client_id or self.config.get("client_id", "")
        self.client_secret = client_secret or self.config.get("client_secret", "")
        self.cloud_id = self.config.get("cloud_id", "")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    @property
    def api_base(self):
        return f"https://api.atlassian.com/ex/confluence/{self.cloud_id}/wiki/api/v2"

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "audience": "api.atlassian.com",
            "client_id": self.client_id,
            "scope": " ".join(self.scopes),
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, json={
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            })
            data = resp.json()
            if "error" in data:
                raise Exception(f"Confluence OAuth error: {data.get('error')}")

            # Get cloud ID
            cloud_id = await self._get_cloud_id(data["access_token"])

            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_at": datetime.utcnow().timestamp() + data.get("expires_in", 3600),
                "cloud_id": cloud_id,
            }

    async def _get_cloud_id(self, token: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {token}"},
            )
            resources = resp.json()
            if resources:
                return resources[0]["id"]
            return ""

    async def refresh_access_token(self) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.token_url, json={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            })
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", self.refresh_token),
            }

    async def test_connection(self) -> Tuple[bool, str]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.api_base}/spaces", params={"limit": 1}, headers=self._headers())
                if resp.status_code == 200:
                    return True, "Connected to Confluence"
                return False, f"API error: {resp.status_code}"
        except Exception as e:
            return False, str(e)

    async def fetch_documents(self, since: Optional[datetime] = None) -> List[Document]:
        documents = []

        async with httpx.AsyncClient(timeout=60) as client:
            # Get pages
            cursor = None
            while True:
                params = {"limit": 50, "body-format": "storage"}
                if cursor:
                    params["cursor"] = cursor

                resp = await client.get(f"{self.api_base}/pages", params=params, headers=self._headers())
                if resp.status_code != 200:
                    break

                data = resp.json()
                for page in data.get("results", []):
                    title = page.get("title", "Untitled")
                    body = page.get("body", {}).get("storage", {}).get("value", "")

                    # Strip HTML tags (basic)
                    clean_text = re.sub(r'<[^>]+>', ' ', body)
                    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

                    if clean_text and len(clean_text) > 30:
                        doc = Document(
                            external_id=f"confluence:{page['id']}",
                            title=title,
                            content=clean_text[:50000],
                            source_url=page.get("_links", {}).get("webui", ""),
                            source_type="wiki",
                            metadata={
                                "space_id": page.get("spaceId"),
                                "version": page.get("version", {}).get("number"),
                            },
                        )
                        documents.append(doc)

                cursor = data.get("_links", {}).get("next")
                if not cursor or len(documents) >= 500:
                    break

        logger.info(f"Confluence: fetched {len(documents)} pages")
        return documents

    async def fetch_document_by_id(self, external_id: str) -> Optional[Document]:
        return None
