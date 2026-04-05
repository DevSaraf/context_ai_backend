import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Primary and fallback embedding models
# Google renamed models — gemini-embedding-001 replaces text-embedding-004
EMBEDDING_MODELS = [
    "gemini-embedding-001",
    "text-embedding-004",
    "embedding-001",
]

EMBEDDING_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Cache which model works so we don't retry on every call
_working_model = None


def create_embedding(text: str) -> list:
    """
    Create embedding using Google's free embedding API.
    Returns 768-dimension vector.
    No torch, no sentence-transformers, no RAM issues.
    Tries multiple model names in case one is unavailable.
    """
    global _working_model

    if not GEMINI_API_KEY:
        print("Warning: GEMINI_API_KEY not set")
        return [0.0] * 768

    # If we already found a working model, try it first
    models_to_try = [_working_model] + EMBEDDING_MODELS if _working_model else EMBEDDING_MODELS

    for model in models_to_try:
        if not model:
            continue
        try:
            url = f"{EMBEDDING_BASE_URL}/{model}:embedContent?key={GEMINI_API_KEY}"

            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    url,
                    json={
                        "content": {
                            "parts": [{"text": text[:2000]}]
                        },
                        "outputDimensionality": 768,
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    values = data["embedding"]["values"]

                    # Cache the working model
                    if _working_model != model:
                        _working_model = model
                        print(f"Embedding model set to: {model} ({len(values)} dims)")

                    return values

                elif response.status_code == 404:
                    print(f"Embedding model '{model}' not found, trying next...")
                    continue
                else:
                    print(f"Embedding API error {response.status_code} with model '{model}': {response.text[:200]}")
                    continue

        except Exception as e:
            print(f"Embedding error with model '{model}': {e}")
            continue

    # All models failed
    print("All embedding models failed. Returning zero vector.")
    return [0.0] * 768