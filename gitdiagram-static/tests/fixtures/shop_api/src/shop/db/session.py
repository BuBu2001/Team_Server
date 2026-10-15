"""Database session management."""

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://shop:shop@localhost/shop")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def init_db() -> None:
    """Create missing tables."""
    from shop.db.models import Base

    Base.metadata.create_all(engine)


@contextmanager
def get_session():
    """Yield a transactional session."""
    session = Session()
    try:
        yield session
        session.commit()
    finally:
        session.close()
