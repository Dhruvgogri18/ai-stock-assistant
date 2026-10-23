from openai import OpenAI

from .config import settings


def configured() -> bool:
    return bool(settings.llm_api_key and settings.llm_model)


def client() -> OpenAI:
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=60)
