# Zendesk AI Agent Service

FastAPI service that bridges Zendesk Conversations webhooks with an AI (RAG) backend, supporting human/AI agent handoff.

## Architecture

- **FastAPI:** Receives webhooks from Zendesk Conversations Integration and exposes handoff management endpoints.
- **PostgreSQL (via `base_infra`):** Stores conversation state, messages, and buffers (5 tables: `tenants`, `conversations`, `messages`, `message_buffer`, `webhook_events`).
- **ARQ / Redis (via `base_infra`):** Debounces message processing (configurable, default 30s) and handles retries.
- **RAG Service:** External AI service for generating answers from conversation history.
- **OpenTelemetry:** Distributed tracing via OTLP exporter; structured JSON logging via `structlog`.

### Request Flow

1. Zendesk POSTs to `POST /webhook/conversations`
2. Webhook router validates API key, logs event idempotently (`webhook_events`), writes message to `message_buffer`
3. Router enqueues an ARQ job (`flush_buffer`) with Redis TTL-based debounce — each new message resets the timer so only the last message in a burst triggers processing
4. ARQ worker acquires a DB lock on the conversation, calls the RAG service with full conversation history, sends reply via Zendesk Conversations API, clears the buffer
5. Conversation state (`agent_mode`: `"ai"` | `"human"`, `is_first_msg_sent`) is persisted in PostgreSQL

## Setup

### 1. Prerequisites

- [uv](https://github.com/astral-sh/uv) installed
- Docker and Docker Compose installed
- `base_infra` project running

### 2. Environment Configuration

Copy `.env.example` to `.env` and fill in the secrets:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | asyncpg-compatible PostgreSQL DSN |
| `REDIS_URL` | Redis DSN |
| `CONVERSATIONS_WEBHOOK_SECRET` | Zendesk webhook shared secret |
| `SUNCO_KEY_ID` / `SUNCO_KEY_SECRET` | Sunshine Conversations API credentials |
| `SUNCO_APP_ID` | Sunshine Conversations app ID |
| `INTEGRATION_KEY_ID` / `INTEGRATION_KEY_SECRET` | Integration credentials |
| `ZENDESK_SUBDOMAIN` | Your Zendesk subdomain |
| `ZENDESK_API_TOKEN` | Zendesk REST API token |
| `ZENDESK_AGENT_GROUP_ID` | Group ID for human handoff routing |
| `RAG_BASE_URL` / `RAG_API_KEY` | meowRAG service URL and key |
| `FLUSH_BUFFER_DEBOUNCE_SECONDS` | Debounce window in seconds (default: `30`) |

### 3. Database Migrations

Apply the initial schema to the `zendb` database in `base_infra`:

```bash
docker compose exec -T postgres psql -U chat_app -d zendb < migrations/001_initial_schema.sql
```

### 4. Running the Service

Start the API and Worker using Docker Compose:

```bash
docker compose up -d --build
```

The API is available at `http://localhost/api/zendesk/` (routed via `base_infra` nginx).

## Development

### Local Installation

```bash
uv venv && source .venv/bin/activate && uv sync --all-extras
```

### Running Tests

```bash
uv run pytest tests/          # all tests
uv run pytest tests/ -x -q    # fail-fast, quiet
uv run pytest tests/ -k "handoff"  # pattern filter
```

### Linting & Type Checking

```bash
ruff check .        # lint
ruff check --fix .  # lint + auto-fix
mypy .              # type check
```

### Local Dev Server

```bash
uv run fastapi dev app/main.py --port 8000
```

### Load Simulation

Simulate concurrent users hitting the webhook endpoint:

```bash
python scripts/load_sim.py \
  --api-key "$CONVERSATIONS_WEBHOOK_SECRET" \
  --num-users 10 \
  --messages-per-user 3
```

Options: `--base-url`, `--num-users`, `--messages-per-user`, `--delay-min`, `--delay-max`, `--timeout`.
Exits with code `1` if success rate < 100%.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/conversations` | Receive Zendesk webhook events |
| `GET` | `/handoff/{conversation_id}/status` | Get current agent mode for a conversation |
| `POST` | `/handoff/{conversation_id}/human` | Manually switch conversation to human agent |
| `POST` | `/handoff/{conversation_id}/ai` | Manually return conversation to AI |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |

### Debug Endpoints (non-production only)

| Method | Path | Description |
|---|---|---|
| `GET` | `/debug/postgres` | Table row counts + 5 most recent conversations |
| `GET` | `/debug/conversation/{id}` | Full state: conversation, messages, buffer |
