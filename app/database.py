import os
from dotenv import load_dotenv
import app  # noqa: F401 - Initialize environment hooks
import os
pg_bin = r"C:\Program Files\PostgreSQL\18\bin"
if os.path.exists(pg_bin):
    os.environ["PATH"] = pg_bin + os.pathsep + os.environ.get("PATH", "")
    os.add_dll_directory(pg_bin)
load_dotenv()
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")

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
        conn.execute(text("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS billing_cycle_id INTEGER REFERENCES billing_cycles(id) ON DELETE SET NULL;"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS proration_adjustments (
                id SERIAL PRIMARY KEY,
                subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
                customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                old_plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                new_plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                old_plan_name VARCHAR(100) NOT NULL,
                new_plan_name VARCHAR(100) NOT NULL,
                remaining_days INTEGER NOT NULL DEFAULT 0,
                total_days INTEGER NOT NULL DEFAULT 30,
                unused_credit FLOAT NOT NULL DEFAULT 0.0,
                new_plan_charge FLOAT NOT NULL DEFAULT 0.0,
                net_amount_due FLOAT NOT NULL DEFAULT 0.0,
                invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        conn.commit()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
