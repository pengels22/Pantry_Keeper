import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pantry_keeper.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def database_insert(db, model):
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError("Pantry Keeper supports SQLite and PostgreSQL.")
    return insert(model)


def insert_if_absent(db, model, values, unique_columns):
    """Atomic insert without committing the caller's receipt transaction."""
    statement = database_insert(db, model).values(**values).on_conflict_do_nothing(index_elements=unique_columns)
    return db.execute(statement).rowcount
