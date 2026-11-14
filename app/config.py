"""
Application Configuration Settings.
Loads environment variables from .env with fallback defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/subscription_db",
)
if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+pg8000://").replace("postgresql://", "postgresql+pg8000://")

SECRET_KEY: str = os.getenv(
    "SECRET_KEY",
    "subscription-billing-super-secret-jwt-key-2026-production-token",
)

ALGORITHM: str = os.getenv("ALGORITHM", "HS256")

ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
