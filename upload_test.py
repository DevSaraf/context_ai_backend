import requests

# Use your token from the extension
TOKEN = input("Paste your JWT token from Chrome extension: ").strip()

API_URL = "http://127.0.0.1:8000"

# Test knowledge to upload
knowledge = """
Our company uses FastAPI for backend development. 
We prefer PostgreSQL over MySQL for database needs.
All API endpoints should include proper authentication using JWT tokens.
Our standard response time SLA is under 200ms for API calls.
"""

response = requests.post(
    f"{API_URL}/knowledge/upload",
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}"
    },
    json={"content": knowledge}
)

print("\nResponse:", response.json())
