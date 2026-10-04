from sqlalchemy import Column, Integer, String, Boolean, Date, Time, DateTime, Text, Float
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), nullable=True, unique=True)
    pin_hash = Column(String(128), nullable=False)
    is_owner = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AuthSession(Base):
    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Invite(Base):
    __tablename__ = "invites"

    code = Column(String(16), primary_key=True)
    created_by = Column(Integer, index=True, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=True)
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


class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    key = Column(String(64), nullable=False)
    value = Column(String(256), nullable=False)