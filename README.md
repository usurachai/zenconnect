# Zendesk AI Agent Service

FastAPI service that manages AI ↔ Human conversation state for Zendesk.

## Architecture

- **FastAPI:** Receives webhooks from Zendesk Conversations Integration.
- **PostgreSQL (via `base_infra`):** Stores conversation state, messages, and buffers.
- **ARQ / Redis (via `base_infra`):** Debounces message processing (30s) and handles retries.
- **RAG Service:** External AI service for generating answers.

## Setup

### 1. Prerequisites
- [uv](https://github.com/astral-sh/uv) installed.
- Docker and Docker Compose installed.
- `base_infra` project running.

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in the secrets:
```bash
cp .env.example .env
```

### 3. Database Migrations
Apply the initial schema to the `zendb` database in `base_infra`:
```bash
# Ensure base_infra is running
docker compose exec -T postgres psql -U chat_app -d zendb < migrations/001_initial_schema.sql
```

### 4. Running the Service
Start the API and Worker using Docker Compose:
```bash
docker compose up -d --build
```

The API will be available at `http://localhost/api/zendesk/` (routed via `base_infra` nginx).

## Development

### Local Installation
```bash
uv venv
source .venv/bin/activate
uv sync --all-extras
```

### Running Tests
```bash
uv run pytest tests/
```

### Debugging
Enabled in `development` environment:
- `GET /api/zendesk/debug/postgres` - DB table counts.
- `GET /api/zendesk/debug/conversation/{id}` - Full state for a conversation.
