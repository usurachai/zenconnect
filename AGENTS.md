# AGENTS.md - Agentic Coding Guidelines for zenconnect

This file provides context for AI agents working in this repository.

## Project Overview

- **Project**: Zendesk AI Agent Service
- **Framework**: FastAPI + PostgreSQL + Redis (ARQ)
- **Python**: 3.12
- **Package Manager**: uv

---

## Build / Lint / Test Commands

### Setup
```bash
uv venv
source .venv/bin/activate
uv sync --all-extras
```

### Running All Checks (required before commit)
```bash
ruff check .
mypy .
pytest -x -q
```

### Running Tests
```bash
# All tests
uv run pytest tests/

# Single test file
uv run pytest tests/test_webhook.py

# Single test (specific test function)
uv run pytest tests/test_webhook.py::test_webhook_authorized_empty_payload

# Run with verbose output
uv run pytest tests/ -v

# Run tests matching a pattern
uv run pytest tests/ -k "webhook"
```

### Linting
```bash
# Run ruff linter
ruff check .

# Auto-fix linting issues
ruff check --fix .
```

### Type Checking
```bash
# Run mypy (strict mode enabled)
mypy .
```

### Running the Application
```bash
# Via Docker Compose (production-like)
docker compose up -d --build

# Or directly (development)
uv run fastapi dev app/main.py --port 8000
```

---

## Code Style Guidelines

### General Rules
- **Line Length**: 100 characters max (configured in pyproject.toml)
- **Python Version**: 3.12+ (enforced in pyproject.toml)
- **Use TDD**: Create test case before coding when possible

### Imports
Organize imports in the following order (as seen in codebase):
1. Standard library (`contextlib`, `typing`, `datetime`, `json`, etc.)
2. Third-party packages (`fastapi`, `pydantic`, `structlog`, `asyncpg`, etc.)
3. Local application imports (`app.config`, `app.models`, etc.)

```python
# Example import order
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

from fastapi import FastAPI, HTTPException
import structlog
import asyncpg
from arq import ArqRedis

from app.config import Settings, get_settings
from app.db import get_pool
from app.models import WebhookPayload
from app.services import persistence
```

### Type Annotations
- Use Python 3.12+ type syntax (no need for `from __future__ import annotations`)
- Enable strict mypy checking
- Use `dict[str, Any]` not `Dict[str, Any]`
- Use `list[Foo]` not `List[Foo]`

```python
# Good
async def process_webhook_events(
    payload: WebhookPayload, 
    pool: asyncpg.Pool, 
    redis: ArqRedis, 
    raw_payload: dict[str, Any]
) -> None:
```

### Naming Conventions
- **Functions/Variables**: snake_case (`get_settings`, `webhook_events`)
- **Classes**: PascalCase (`Settings`, `WebhookPayload`)
- **Constants**: SCREAMING_SNAKE_CASE (`ALLOWED_CHANNELS`)
- **Files**: snake_case (`webhook.py`, `persistence.py`)

### Pydantic Models
- Use `BaseModel` for all data models
- Use `pydantic-settings` for configuration
- Use `Optional[...]` with default `None` for optional fields
- Use `Literal[...]` for enum-like string values

```python
class Settings(BaseSettings):
    env: Literal["development", "production", "test"] = "development"
    database_url: str
    redis_url: str
    conversations_webhook_secret: str
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

### Error Handling
- Use `FastAPI.HTTPException` for HTTP errors
- Use structured logging with `structlog`
- Log all errors with appropriate context

```python
import structlog
logger = structlog.get_logger()

# In endpoint
raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")

# In service
try:
    job = await redis.enqueue_job(...)
except Exception as e:
    log.error("Failed to enqueue flush_buffer", job_id=job_id, error=str(e))
```

### Async/Await
- Use `asyncpg` for database operations
- Use `httpx` for HTTP requests
- Always mark async functions with `async def`
- Use `await` for all async operations

### Database Operations
- Use raw SQL queries with asyncpg
- Use parameterized queries (e.g., `$1`, `$2`) - never string interpolation
- Use `ON CONFLICT` for upserts

```python
query = """
    INSERT INTO messages (message_id, conversation_id, author_type, channel, body, received_at)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (message_id) DO NOTHING
"""
await pool.execute(query, msg.id, conv.id, msg.author.type, ...)
```

### Testing
- Use `pytest` with `pytest-asyncio` and `pytest-httpx`
- Use `AsyncClient` from httpx for endpoint testing
- Use `unittest.mock` for mocking
- Use fixtures for test setup

```python
@pytest.mark.asyncio
async def test_webhook_authorized_empty_payload(settings):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/webhook/conversations",
            headers={"x-api-key": settings.conversations_webhook_secret},
            json={"app": {"id": "app_123"}, "webhook": {"id": "wh_123", "version": "v2"}, "events": []}
        )
    assert response.status_code == 200
```

---

## Git Workflow

Follow the git-workflow skill. Key points:
- Always branch from `main`: `git checkout main && git pull`
- Branch naming: `feat/issue-{N}-{slug}` or `fix/issue-{N}-{slug}`
- Run `ruff check .`, `mypy .`, `pytest -x -q` before every commit
- Use conventional commits: `feat(scope): description`, `fix(scope): description`
- Create PRs as draft with `Closes #{N}` in body

---

## Additional Context

### External Services (configured in .env)
- **PostgreSQL**: Via `base_infra` external URL
- **Redis**: Via `base_infra` external URL
- **RAG Service**: External AI service for generating answers

### Key Files
- `app/main.py`: FastAPI application entry point
- `app/config.py`: Settings management
- `app/models.py`: Pydantic data models
- `app/routers/`: API route handlers
- `app/services/`: Business logic
- `app/worker.py`: ARQ background worker

### Debug Endpoints (development only)
- `GET /debug/postgres` - DB table counts
- `GET /debug/conversation/{id}` - Full state for a conversation

---

## Included Agent Rules

The following rules from `.agents/rules/` are always active:
- Use uv as Python package manager
- Use TDD (test case before coding)
- Use gh command for GitHub interaction
- Connect PostgreSQL/Redis via external URLs (no local Docker instances)

---

This file should be updated as the project evolves.