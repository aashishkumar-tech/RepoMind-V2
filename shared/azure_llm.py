"""
shared/azure_llm.py — Azure OpenAI client factory

Provides a unified LLM client that uses Azure OpenAI when credentials
are configured, falling back to Groq for local development.
"""

from typing import Any
from shared.config import settings
from shared.logger import get_logger

logger = get_logger("shared.azure_llm")


def get_llm_client() -> Any:
    """
    Return Azure OpenAI client if Azure credentials are set,
    otherwise return Groq client for local dev fallback.
    """
    if settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY:
        from openai import AzureOpenAI
        logger.info("llm_client_azure", endpoint=settings.AZURE_OPENAI_ENDPOINT)
        return AzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    else:
        from groq import Groq
        logger.info("llm_client_groq_fallback")
        return Groq(api_key=settings.GROQ_API_KEY)


def get_model_name() -> str:
    """Return the correct model name for the active client."""
    if settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_KEY:
        return settings.AZURE_OPENAI_DEPLOYMENT_NAME  # e.g. "gpt-4o"
    return "llama-3.3-70b-versatile"  # Groq fallback
