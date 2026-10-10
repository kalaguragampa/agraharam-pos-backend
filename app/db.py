from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import config

class Base(DeclarativeBase):
    pass

engine = create_engine(config.database_url, pool_pre_ping=True, **({'connect_args': {'check_same_thread': False}} if config.app_env == 'test' and config.database_url.startswith('sqlite') else {}))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
def get_db():
    with SessionLocal() as db:
        yield db
