import os
from dataclasses import dataclass

import google.generativeai as genai
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str


def load_settings() -> Settings:
    load_dotenv()
    return Settings(gemini_api_key=os.getenv("GEMINI_API_KEY", ""))


def configure_gemini(settings: Settings) -> None:
    if settings.gemini_api_key:
        genai.configure(api_key=settings.gemini_api_key)
