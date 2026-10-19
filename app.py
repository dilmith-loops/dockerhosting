from dotenv import load_dotenv
load_dotenv()
import asyncio
import os
import sys
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("skyforge-app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [asyncio.create_task(supervise(name, spec)) for name, spec in PROCESS_SERVICES.items()]
    async with AsyncExitStack() as stack:
        # Mounted sub-apps' own startup (auth creates its tables here) doesn't run by itself
        for prefix, sub in MOUNTED_APPS:
            try:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
                logger.info(f"Ran startup of the app on {prefix}")
            except Exception as e:
                logger.warning(f"Startup of the app on {prefix} failed (its routes may not work): {e}")
        yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await proxy_client.aclose()


app = FastAPI(
    title="SkillBridge Unified Server",
    description="SkyForge Unified Runner for SkillBridge Microservices & APIs",
    version="1.0.0",
    lifespan=lifespan,
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

# Modules the learning and interview services import (import name -> pip name in requirements.txt)
SERVICE_MODULES = ["pgvector", "onnxruntime", "tokenizers", "numpy", "faster_whisper", "pypdf", "docx",
                   "google.genai"]


def ensure_service_packages():
    """Installs requirements.txt when a service module is missing. The panel's own pip step downloads into
    the container's small /tmp, and one large wheel (ctranslate2, 40 MB) failed with "No space left on
    device", which aborts the whole install. Here the temporary files go on the data disk instead."""
    import importlib
    import shutil
    import subprocess
    missing = []
    for module in SERVICE_MODULES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if not missing:
        return
    logger.info(f"Installing packages for the services (missing: {', '.join(missing)})...")
    tmp = os.path.join(BASE_DIR, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TMPDIR=tmp, PIP_NO_CACHE_DIR="1")
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--prefix", os.path.join(BASE_DIR, ".local"),
                             "-r", os.path.join(BASE_DIR, "requirements.txt")], env=env)
    shutil.rmtree(tmp, ignore_errors=True)
    importlib.invalidate_caches()
    if result.returncode:
        logger.warning(f"pip install failed (exit {result.returncode}); the learning and interview services "
                       "will keep restarting until their packages are installed")


ensure_service_packages()

logger.info(f"Base Directory: {BASE_DIR}")
logger.info(f"Services Directory: {SERVICES_DIR}")

def mount_keep_prefix(prefix: str, sub):
    """Mounts sub at prefix while it still sees the full path. Auth and the job engine declare their routes
    with the prefix (/auth/login, /jobs/...), so a plain mount only answered /auth/auth/login. Paths the
    sub-app knows only without the prefix (auth's /health) still reach it as before."""
    from starlette.routing import Match

    async def asgi(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            outer_root = scope.get("root_path", "")
            if outer_root.endswith(prefix):
                outer_root = outer_root[:-len(prefix)]
            # Newer Starlette keeps the full path in scope["path"]; older versions strip the mount prefix
            full = scope["path"] if scope["path"].startswith(prefix + "/") else prefix + scope["path"]
            candidate = dict(scope, path=full, root_path=outer_root)
            if any(route.matches(candidate)[0] == Match.FULL for route in sub.router.routes):
                scope = candidate
        await sub(scope, receive, send)

    app.mount(prefix, asgi)
    MOUNTED_APPS.append((prefix, sub))


MOUNTED_APPS = []


def mount_subapps():
    # 1. Job Market Intelligence Engine (/jobs)
    try:
        job_dir = os.path.join(SERVICES_DIR, "job-market-intelligence-engine")
        if os.path.isdir(job_dir):
            if job_dir not in sys.path:
                sys.path.insert(0, job_dir)
            import app.main as jobs_module
            if hasattr(jobs_module, "app"):
                mount_keep_prefix("/jobs", jobs_module.app)
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
                mount_keep_prefix("/auth", auth_module.create_app())
                logger.info("Successfully mounted Auth Service on /auth")
            elif hasattr(auth_module, "app"):
                mount_keep_prefix("/auth", auth_module.app)
                logger.info("Successfully mounted Auth Service on /auth")
    except Exception as e:
        logger.warning(f"Auth Service not mounted (DB connection or env variables pending): {e}")

mount_subapps()


# ---------------------------------------------------------------- learning and interview services
# These run as their own processes on internal ports, and this server forwards their routes (the same
# routes the nginx gateway sends them in Docker). They can't be mounted like auth: both use a top-level
# package named "app" (one process can hold only one), they import modules inside functions at request
# time, and their startup work (tables, corpus ingestion) is a lifespan that mounted sub-apps never run.
# A variable prefixed LEARNING_ or INTERVIEW_ overrides the unprefixed one for that service only,
# e.g. LEARNING_POSTGRES_DB=learning_db, INTERVIEW_POSTGRES_DB=interview_db.
PROCESS_SERVICES = {
    "learning-service": {"port": 8101, "env_prefix": "LEARNING_", "routes": [
        "/api/v1/learning", "/api/v1/progress", "/api/v1/research", "/api/v1/upstream", "/api/v1/analytics"]},
    "interview-service": {"port": 8102, "env_prefix": "INTERVIEW_", "routes": ["/api/v1/interview"]},
}
SERVICE_STATE = {name: "starting" for name in PROCESS_SERVICES}
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length"}
# Lessons and quizzes wait on the LLM for up to a couple of minutes
proxy_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0))


async def supervise(name: str, spec: dict):
    """Runs a service with uvicorn and restarts it if it exits (backing off up to a minute)."""
    service_dir = os.path.join(SERVICES_DIR, name)
    env = dict(os.environ, SERVICE_PORT=str(spec["port"]), PYTHONUNBUFFERED="1")
    env.update({k[len(spec["env_prefix"]):]: v for k, v in os.environ.items() if k.startswith(spec["env_prefix"])})
    delay = 5
    while True:
        SERVICE_STATE[name] = "starting"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(spec["port"]),
            cwd=service_dir, env=env)
        logger.info(f"Started {name} (pid {proc.pid}) on 127.0.0.1:{spec['port']}")
        SERVICE_STATE[name] = "running"
        try:
            code = await proc.wait()
        except asyncio.CancelledError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
            raise
        SERVICE_STATE[name] = f"exited ({code}), restarting in {delay}s"
        logger.warning(f"{name} exited with code {code}; restarting in {delay}s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def make_proxy(name: str, port: int):
    async def proxy(request: Request, path: str = ""):
        url = f"http://127.0.0.1:{port}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        try:
            upstream = await proxy_client.send(
                proxy_client.build_request(request.method, url, headers=headers, content=await request.body()),
                stream=True)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=503, content={
                "detail": f"{name} is not available yet ({SERVICE_STATE[name]}): {e.__class__.__name__}"})

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        # This server's CORS middleware answers for every route; a second set of CORS headers from the
        # service would make browsers reject the response
        out = {k: v for k, v in upstream.headers.items()
               if k.lower() not in HOP_HEADERS and not k.lower().startswith("access-control-")}
        return StreamingResponse(body(), status_code=upstream.status_code, headers=out)
    return proxy


for _name, _spec in PROCESS_SERVICES.items():
    for _route in _spec["routes"]:
        app.add_api_route(_route + "/{path:path}", make_proxy(_name, _spec["port"]),
                          methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
                          include_in_schema=False)


@app.get("/health/services")
def services_health():
    """Whether the learning and interview processes are running (they also report their own /health)."""
    return SERVICE_STATE

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
