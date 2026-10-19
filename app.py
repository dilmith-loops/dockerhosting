import os
import sys
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("skyforge-app")

app = FastAPI(
    title="SkillBridge Unified Server",
    description="SkyForge Unified Runner for SkillBridge Microservices & APIs",
    version="1.0.0",
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Base health & info endpoints (SkyForge ping target)
@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "SkillBridge Python Server",
        "platform": "SkyForge",
        "docs": "/docs",
        "health": "/health",
        "routes": {
            "auth": "/auth",
            "jobs": "/jobs",
            "readiness": "/readiness",
            "shared_skills": "/ssks",
            "learning": "/api/v1/learning",
            "interview": "/api/v1/interview"
        }
    }

@app.get("/health")
def health_check():
    """Health check endpoint used by SkyForge to confirm container is healthy."""
    return {
        "status": "healthy",
        "server": "SkyForge Python 3.13",
        "service": "SkillBridge"
    }

# Dynamically link services/ paths into sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICES_DIR = os.path.join(BASE_DIR, "services")

logger.info(f"Base Directory: {BASE_DIR}")
logger.info(f"Services Directory: {SERVICES_DIR}")

# Graceful Service Mounting
def mount_subapps():
    # 1. Job Market Intelligence Engine (/jobs)
    try:
        job_dir = os.path.join(SERVICES_DIR, "job-market-intelligence-engine")
        if os.path.isdir(job_dir):
            sys.path.insert(0, job_dir)
            from app.main import app as jobs_app
            app.mount("/jobs", jobs_app)
            logger.info("Successfully mounted Job Market Intelligence Engine on /jobs")
    except Exception as e:
        logger.warning(f"Could not mount Job Market Intelligence Engine: {e}")

    # 2. Auth Service (/auth)
    try:
        auth_dir = os.path.join(SERVICES_DIR, "auth-service")
        if os.path.isdir(auth_dir):
            sys.path.insert(0, auth_dir)
            from app.main import create_app as create_auth_app
            app.mount("/auth", create_auth_app())
            logger.info("Successfully mounted Auth Service on /auth")
    except Exception as e:
        logger.warning(f"Could not mount Auth Service (check DB config): {e}")

mount_subapps()

if __name__ == "__main__":
    # SkyForge passes the assigned port via SERVER_PORT or PORT, default to 25567
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 25567)))
    host = os.environ.get("SERVER_IP", "0.0.0.0")

    print("=================================")
    print("SKYFORGE PYTHON SERVER")
    print("=================================")
    print(f"Host: {host}")
    print(f"Port: {port}")
    print(f"HTTP Server listening on {host}:{port}")
    print("SkyForge application starting...")

    uvicorn.run(app, host=host, port=port)
