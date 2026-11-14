import os
from dotenv import load_dotenv
import app  # noqa: F401 - Initialize environment hooks
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+pg8000://").replace("postgresql://", "postgresql+pg8000://")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def run_migrations():
    """
    Safely adds missing columns to PostgreSQL without destroying existing data.
    """
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;"))
        conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS trial_end_date TIMESTAMPTZ;"))
        conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;"))
        conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;"))
        conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;"))
        conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ;"))
        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
