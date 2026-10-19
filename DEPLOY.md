# Deploying on the SkyForge Python server

`app.py` is the single entry point the panel runs (Python 3.13, one public port, `SERVER_PORT`):

| Path | Served by |
|---|---|
| `/auth/...` | auth-service, mounted in this process |
| `/jobs/...` | job-market-intelligence-engine, mounted in this process |
| `/api/v1/learning`, `/progress`, `/research`, `/upstream`, `/analytics` | learning-service, own process on 127.0.0.1:8101 |
| `/api/v1/interview/...` | interview-service, own process on 127.0.0.1:8102 |
| `/health`, `/health/services` | this server; the second shows whether the two processes are running |

Learning and interview run as separate processes (restarted if they exit) because both use a top-level
package named `app`, import modules inside functions at request time, and need their startup (table
creation, corpus ingestion), which mounted sub-apps never run. Measured memory with every model loaded:
about 1 GB in total (runner 76 MB, interview 461 MB, learning 470 MB), plus about 0.5 GB once Whisper
transcribes the first answer.

## What the server needs

1. **PostgreSQL with pgvector** (the panel has none): a hosted one such as Neon or Supabase, with the
   `vector` extension and four databases: `auth_db`, `ssks_db`, `learning_db`, `interview_db`.
2. **The model files**, which git ignores (`*.onnx`): copy these four folders to the same paths on the
   server: `services/learning-service/models/{embedder,reranker}`,
   `services/interview-service/models/{embedder,answer_scorer}` (about 400 MB).
3. **The language model**: Gemma on the developer's Mac through the model-host tunnel
   (`scripts/model-host/start-model-host.sh`), or `LLM_PROVIDER=gemini` with a `GEMINI_API_KEY`.

## Environment variables

A `LEARNING_` or `INTERVIEW_` prefix sets a variable for that service only.

```
# Database (hosted Postgres)
POSTGRES_HOST=<host>
POSTGRES_PORT=5432
POSTGRES_USER=<user>
POSTGRES_PASSWORD=<password>
POSTGRES_SSL=require
LEARNING_POSTGRES_DB=learning_db
INTERVIEW_POSTGRES_DB=interview_db
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/auth_db?ssl=require   # auth-service
JWT_SECRET=<48+ random characters>

# Models
EMBEDDING_PROVIDER=local          # the fine-tuned embedder in models/embedder
EMBEDDING_DIMENSION=384
LLM_PROVIDER=local                # Gemma on the Mac, through the tunnel
LOCAL_LLM_URL=https://<tunnel>/llm
MODEL_HOST_KEY=<from ~/.skillbridge-local/model-host.key on the Mac>
STT_PROVIDER=local                # faster-whisper on the server (or gemini)
AVATAR_PROVIDER=none              # the avatar's videos are too heavy for the tunnel
```

The vectors in the database must come from the same embedding model the services use. Restoring a
dump of the local `learning_db` and `interview_db` (made with the fine-tuned embedder) avoids
re-embedding the 25,560-chunk corpus on the server's CPU.
