"""
Context AI Backend — Main Application
All route logic lives in app/routers/. This file just wires them together.
"""

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from app.database import Base, engine

# Create tables
Base.metadata.create_all(bind=engine)

# Initialize app
app = FastAPI(
    title="Context AI",
    description="AI-powered company knowledge platform",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== STATIC FILES ==============

@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "landing.html"))


@app.get("/app")
async def dashboard_app():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html"))


@app.get("/health")
def health():
    return {"message": "Context AI backend running"}


@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard.html"))


@app.get("/upload_test.html")
async def serve_upload_test():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "upload_test.html"))


# ============== REGISTER ROUTERS ==============

from app.routers.auth_router import router as auth_router
from app.routers.knowledge_router import router as knowledge_router
from app.routers.search_router import router as search_router
from app.routers.feedback_router import router as feedback_router
from app.routers.zendesk_router import router as zendesk_router

app.include_router(auth_router)
app.include_router(knowledge_router)
app.include_router(search_router)
app.include_router(feedback_router)
app.include_router(zendesk_router)
