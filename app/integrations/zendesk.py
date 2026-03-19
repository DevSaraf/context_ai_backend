"""
Zendesk Integration - OAuth and Ticket Import
"""
import os
import httpx
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

# Zendesk OAuth Configuration
ZENDESK_CLIENT_ID = os.getenv("ZENDESK_CLIENT_ID", "")
ZENDESK_CLIENT_SECRET = os.getenv("ZENDESK_CLIENT_SECRET", "")
ZENDESK_REDIRECT_URI = os.getenv("ZENDESK_REDIRECT_URI", "http://localhost:8000/integrations/zendesk/callback")


class ZendeskClient:
    """Client for interacting with Zendesk API"""
    
    def __init__(self, subdomain: str, access_token: str):
        self.subdomain = subdomain
        self.access_token = access_token
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    
    async def get_tickets(self, page: int = 1, per_page: int = 100) -> Dict[str, Any]:
        """Fetch tickets from Zendesk"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/tickets.json",
                headers=self.headers,
                params={"page": page, "per_page": per_page, "sort_by": "updated_at", "sort_order": "desc"}
            )
            response.raise_for_status()
            return response.json()
    
    async def get_ticket_comments(self, ticket_id: int) -> Dict[str, Any]:
        """Fetch comments for a specific ticket"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/tickets/{ticket_id}/comments.json",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()
    
    async def get_satisfaction_ratings(self, page: int = 1) -> Dict[str, Any]:
        """Fetch CSAT ratings"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/satisfaction_ratings.json",
                headers=self.headers,
                params={"page": page}
            )
            response.raise_for_status()
            return response.json()
    
    async def verify_connection(self) -> bool:
        """Test if the connection is valid"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/users/me.json",
                    headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False


def get_oauth_url(subdomain: str, state: str) -> str:
    """Generate Zendesk OAuth authorization URL"""
    params = {
        "response_type": "code",
        "redirect_uri": ZENDESK_REDIRECT_URI,
        "client_id": ZENDESK_CLIENT_ID,
        "scope": "read tickets:read users:read",
        "state": state
    }
    return f"https://{subdomain}.zendesk.com/oauth/authorizations/new?{urlencode(params)}"


async def exchange_code_for_token(subdomain: str, code: str) -> Dict[str, Any]:
    """Exchange authorization code for access token"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://{subdomain}.zendesk.com/oauth/tokens",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": ZENDESK_CLIENT_ID,
                "client_secret": ZENDESK_CLIENT_SECRET,
                "redirect_uri": ZENDESK_REDIRECT_URI,
                "scope": "read tickets:read users:read"
            }
        )
        response.raise_for_status()
        return response.json()


def calculate_resolution_score(csat_score: Optional[int]) -> float:
    """Convert CSAT score (1-5) to resolution score (0.0-1.0)"""
    if csat_score is None:
        return 0.5  # Neutral score for unrated tickets
    
    # Map: 1=0.0, 2=0.25, 3=0.5, 4=0.75, 5=1.0
    return (csat_score - 1) / 4.0


def format_ticket_for_embedding(ticket: Dict, comments: List[Dict]) -> str:
    """Format ticket data into text suitable for embedding"""
    parts = []
    
    # Subject
    subject = ticket.get("subject", "No subject")
    parts.append(f"Subject: {subject}")
    
    # Description (first comment is usually the original request)
    description = ticket.get("description", "")
    if description:
        parts.append(f"\nCustomer Issue:\n{description[:1000]}")  # Truncate long descriptions
    
    # Resolution (last public comment from agent, if ticket is solved)
    if ticket.get("status") in ["solved", "closed"]:
        agent_comments = [c for c in comments if not c.get("public", True) == False and c.get("author_id") != ticket.get("requester_id")]
        if agent_comments:
            last_resolution = agent_comments[-1].get("body", "")[:1000]
            parts.append(f"\nResolution:\n{last_resolution}")
    
    # Add metadata
    parts.append(f"\nStatus: {ticket.get('status', 'unknown')}")
    if ticket.get("tags"):
        parts.append(f"Tags: {', '.join(ticket['tags'][:10])}")
    
    return "\n".join(parts)
