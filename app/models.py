from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    company_id = Column(String)
    api_key = Column(String, unique=True)

# class KnowledgeItem(Base):
#     __tablename__ = "knowledge_items"

#     id = Column(Integer, primary_key=True, index=True)
#     company_id = Column(String, index=True)

#     title = Column(String)
#     content = Column(Text)
#     source = Column(String)

#     embedding = Column(Vector(384))

#     created_at = Column(DateTime(timezone=True), server_default=func.now())

class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)

    company_id = Column(String, index=True)

    source_type = Column(String)  
    source_id = Column(Integer)

    text = Column(Text)

    embedding = Column(Vector(384))

    created_at = Column(DateTime(timezone=True), server_default=func.now())