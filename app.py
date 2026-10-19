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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICES_DIR = os.path.join(BASE_DIR, "services")

logger.info(f"Base Directory: {BASE_DIR}")
logger.info(f"Services Directory: {SERVICES_DIR}")

def mount_subapps():
    # 1. Job Market Intelligence Engine (/jobs)
    try:
        job_dir = os.path.join(SERVICES_DIR, "job-market-intelligence-engine")
        if os.path.isdir(job_dir):
            if job_dir not in sys.path:
                sys.path.insert(0, job_dir)
            import app.main as jobs_module
            if hasattr(jobs_module, "app"):
                app.mount("/jobs", jobs_module.app)
                logger.info("Successfully mounted Job Market Intelligence Engine on /jobs")
    except Exception as e:
        logger.warning(f"Could not mount Job Market Intelligence Engine: {e}")

    # 2. Auth Service (/auth)
    try:
        auth_dir = os.path.join(SERVICES_DIR, "auth-service")
        if os.path.isdir(auth_dir):
            # Clear previously loaded 'app' module to prevent namespace collision
            for mod_name in list(sys.modules.keys()):
                if mod_name == "app" or mod_name.startswith("app."):
                    del sys.modules[mod_name]
            if auth_dir not in sys.path:
                sys.path.insert(0, auth_dir)
            import app.main as auth_module
            if hasattr(auth_module, "create_app"):
                app.mount("/auth", auth_module.create_app())
                logger.info("Successfully mounted Auth Service on /auth")
            elif hasattr(auth_module, "app"):
                app.mount("/auth", auth_module.app)
                logger.info("Successfully mounted Auth Service on /auth")
    except Exception as e:
        logger.warning(f"Auth Service not mounted (DB connection or env variables pending): {e}")

mount_subapps()

if __name__ == "__main__":
    # SkyForge passes the assigned port via SERVER_PORT or PORT, default to 25567
    port = int(os.environ.get("SERVER_PORT", os.environ.get("PORT", 25567)))
    
    # CRITICAL: Always bind to 0.0.0.0 inside Docker container.
    # Do NOT bind to SERVER_IP (5.181.180.52), as external IPs do not belong to container eth0 and cause Errno 99!
    host = "0.0.0.0"

    print("=================================")
    print("SKYFORGE PYTHON SERVER")
    print("=================================")
    print(f"Host: {host}")
    print(f"Port: {port}")
    print(f"HTTP Server listening on {host}:{port}")
    print("SkyForge application starting...")

    uvicorn.run(app, host=host, port=port)
