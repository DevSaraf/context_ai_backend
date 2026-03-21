import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent"


def create_embedding(text: str) -> list:
    """
    Create embedding using Google's free embedding API.
    Returns 768-dimension vector.
    No torch, no sentence-transformers, no RAM issues.
    """
    try:
        # Use synchronous httpx since some callers are sync
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{EMBEDDING_URL}?key={GEMINI_API_KEY}",
                json={
                    "content": {
                        "parts": [{"text": text[:2000]}]  # Gemini has input limits
                    }
                }
            )

            if response.status_code != 200:
                print(f"Embedding API error {response.status_code}: {response.text[:200]}")
                # Return zero vector as fallback
                return [0.0] * 768

            data = response.json()
            return data["embedding"]["values"]

    except Exception as e:
        print(f"Embedding error: {e}")
        return [0.0] * 768
