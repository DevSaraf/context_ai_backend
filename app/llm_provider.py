"""
LLM Provider Module
Abstracts LLM calls behind a single interface.
Supports: Gemini (default/free), Claude, Ollama (local)

Switch providers by changing LLM_PROVIDER in .env:
    LLM_PROVIDER=gemini        (default, free tier)
    LLM_PROVIDER=claude
    LLM_PROVIDER=ollama

Dependencies:
    pip install google-generativeai    (for Gemini)
    # pip install anthropic            (for Claude, optional)
    # Ollama just uses httpx which you already have
"""

import os
import httpx
from typing import Optional
from dotenv import load_dotenv
from abc import ABC, abstractmethod

load_dotenv()

# ============== CONFIGURATION ==============

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")


# ============== BASE INTERFACE ==============

class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        """Generate a response given system and user prompts."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and ready."""
        pass


# ============== GEMINI PROVIDER ==============

class GeminiProvider(LLMProvider):
    """Google Gemini API (free tier: 15 RPM on Flash)."""

    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        """Call Gemini API using REST (no SDK dependency issues)."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_url}?key={self.api_key}",
                    json={
                        "system_instruction": {
                            "parts": [{"text": system_prompt}]
                        },
                        "contents": [
                            {
                                "parts": [{"text": user_prompt}]
                            }
                        ],
                        "generationConfig": {
                            "maxOutputTokens": max_tokens,
                            "temperature": 0.3,
                        }
                    }
                )

                if response.status_code != 200:
                    error_detail = response.text[:200]
                    print(f"Gemini API error {response.status_code}: {error_detail}")
                    return f"Error generating answer (Gemini {response.status_code}). Please try again."

                data = response.json()

                # Extract text from Gemini response
                candidates = data.get("candidates", [])
                if not candidates:
                    return "No response generated. The query may be too broad — try being more specific."

                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    return "Empty response from AI. Please try rephrasing your question."

                return parts[0].get("text", "").strip()

        except httpx.TimeoutException:
            return "AI response timed out. Please try again with a shorter query."
        except Exception as e:
            print(f"Gemini generation error: {e}")
            return f"Error generating answer: {str(e)}"


# ============== CLAUDE PROVIDER ==============

class ClaudeProvider(LLMProvider):
    """Anthropic Claude API."""

    def __init__(self):
        self.api_key = CLAUDE_API_KEY
        self.model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "max_tokens": max_tokens,
                        "system": system_prompt,
                        "messages": [
                            {"role": "user", "content": user_prompt}
                        ]
                    }
                )

                if response.status_code != 200:
                    error_detail = response.text[:200]
                    print(f"Claude API error {response.status_code}: {error_detail}")
                    return f"Error generating answer (Claude {response.status_code})."

                data = response.json()
                content = data.get("content", [])
                if content:
                    return content[0].get("text", "").strip()
                return "No response generated."

        except httpx.TimeoutException:
            return "AI response timed out. Please try again."
        except Exception as e:
            print(f"Claude generation error: {e}")
            return f"Error generating answer: {str(e)}"


# ============== OLLAMA PROVIDER ==============

class OllamaProvider(LLMProvider):
    """Local Ollama server (completely free, runs on your machine)."""

    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.model = OLLAMA_MODEL

    def is_available(self) -> bool:
        try:
            import httpx as hx
            r = hx.get(f"{self.base_url}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    async def generate(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": 0.3,
                        }
                    }
                )

                if response.status_code != 200:
                    return f"Ollama error ({response.status_code}). Is Ollama running?"

                data = response.json()
                return data.get("message", {}).get("content", "").strip()

        except httpx.ConnectError:
            return "Could not connect to Ollama. Make sure it's running (ollama serve)."
        except Exception as e:
            print(f"Ollama generation error: {e}")
            return f"Error: {str(e)}"


# ============== FACTORY ==============

def get_llm_provider() -> LLMProvider:
    """Get the configured LLM provider."""
    providers = {
        "gemini": GeminiProvider,
        "claude": ClaudeProvider,
        "ollama": OllamaProvider,
    }

    provider_class = providers.get(LLM_PROVIDER)
    if not provider_class:
        print(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}. Falling back to Gemini.")
        provider_class = GeminiProvider

    provider = provider_class()

    if not provider.is_available():
        print(f"Warning: {LLM_PROVIDER} provider is not configured or not available.")
        # Try fallback order: gemini -> ollama
        for fallback_name in ["gemini", "ollama"]:
            if fallback_name != LLM_PROVIDER:
                fallback = providers[fallback_name]()
                if fallback.is_available():
                    print(f"Falling back to {fallback_name}")
                    return fallback

    return provider


# Singleton instance — import this in other modules
llm = get_llm_provider()
