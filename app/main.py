"""
Context AI Backend — Main Application
All route logic lives in app/routers/. This file just wires them together.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before any other imports

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from app.database import Base, engine

# Create tables
Base.metadata.create_all(bind=engine)

# Initialize app
app = FastAPI(
    title="KRAB - AI Knowledge Platform",
    description="AI-powered company knowledge platform with connectors, tickets, and help center",
    version="2.0.0"
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


@app.get("/widget")
async def serve_widget():
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(__file__)), "widget.html"))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy — KRAB</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #262624; color: #e8e6e1; line-height: 1.8; padding: 40px 20px; }
        .container { max-width: 720px; margin: 0 auto; }
        h1 { color: #c6613f; font-size: 28px; letter-spacing: 2px; margin-bottom: 8px; }
        .updated { color: #6a6960; font-size: 14px; margin-bottom: 40px; }
        h2 { color: #c6613f; font-size: 18px; margin-top: 32px; margin-bottom: 12px; }
        p { color: #c2c0b6; font-size: 15px; margin-bottom: 16px; }
        ul { margin: 0 0 16px 24px; color: #c2c0b6; font-size: 15px; }
        li { margin-bottom: 8px; }
        a { color: #c6613f; }
    </style>
</head>
<body>
<div class="container">
<h1>KRAB — Privacy Policy</h1>
<p class="updated">Last updated: April 3, 2026</p>
<h2>Overview</h2>
<p>KRAB is a browser extension that surfaces relevant company knowledge inside AI chat platforms. We are committed to protecting your privacy.</p>
<h2>Data we collect</h2>
<ul>
<li><b>Account information:</b> Email address and company identifier, provided during registration.</li>
<li><b>Authentication token:</b> Stored locally in your browser via Chrome storage API to keep you logged in. Never shared with third parties.</li>
<li><b>Prompts:</b> Your prompt text is sent to the KRAB backend to search your knowledge base. Prompts are used only for real-time retrieval and are not stored permanently.</li>
<li><b>Feedback:</b> Which knowledge chunks were used or rated, to improve search relevance.</li>
</ul>
<h2>Data we do NOT collect</h2>
<ul>
<li>We do not store passwords in plain text — they are hashed.</li>
<li>We do not read, store, or transmit your chat history or AI responses.</li>
<li>We do not collect browsing history or data from non-supported websites.</li>
<li>We do not serve ads or share data with advertisers.</li>
<li>We do not sell your data to any third party.</li>
</ul>
<h2>Permissions</h2>
<ul>
<li><b>storage:</b> Stores authentication token and preferences locally.</li>
<li><b>activeTab:</b> Detects the active chat platform to inject the sidebar.</li>
<li><b>tabs:</b> Notifies open tabs when auth state changes.</li>
<li><b>Host permissions:</b> Injects sidebar into ChatGPT, Claude, Gemini and communicates with the KRAB API.</li>
</ul>
<h2>Third-party services</h2>
<p>KRAB uses Google Gemini models for embeddings and answer generation. Data is processed only for retrieval and not retained beyond the API request.</p>
<h2>Data deletion</h2>
<p>Contact support@krabai.tech to delete your account and data. Logging out removes all local data immediately.</p>
<h2>Contact</h2>
<p>Questions? Email <a href="mailto:saraf.dev.a@gmail.com">support@krabai.tech</a>.</p>
</div>
</body>
</html>"""


# ============== REGISTER ROUTERS ==============

from app.routers.auth_router import router as auth_router
from app.routers.knowledge_router import router as knowledge_router
from app.routers.search_router import router as search_router
from app.routers.feedback_router import router as feedback_router
from app.routers.zendesk_router import router as zendesk_router
from app.routers.widget_router import router as widget_router
from app.routers.connector_router import router as connector_router
from app.routers.ticket_router import router as ticket_router
from app.routers.helpcenter_router import help_router, public_help_router, health_router

app.include_router(auth_router)
app.include_router(knowledge_router)
app.include_router(search_router)
app.include_router(feedback_router)
app.include_router(zendesk_router)
app.include_router(widget_router)
app.include_router(connector_router)
app.include_router(ticket_router)
app.include_router(help_router)
app.include_router(public_help_router)
app.include_router(health_router)