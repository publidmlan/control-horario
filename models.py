from sqlalchemy import Column, Integer, String, Date, Time, DateTime, Text, Float
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    pin_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    time_in = Column(Time, nullable=False)
    time_out = Column(Time, nullable=True)
    entry_type = Column(String(20), nullable=False, server_default="presencial")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(256), nullable=False)
