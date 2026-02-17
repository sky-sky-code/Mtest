import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from sqlalchemy.orm import Mapped, mapped_column
from config import POSTGRES_URL


class Base(DeclarativeBase):
    uid: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, primary_key=True)


engine = create_engine(POSTGRES_URL)
Session = sessionmaker(engine)
