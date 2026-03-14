FROM python:3.12-slim

WORKDIR /app

# Install uv and curl
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy dependencies definitions
COPY pyproject.toml .

# create a virtual environment and install dependencies
RUN uv venv
RUN uv pip install -e .

# Copy application code
COPY . /app

# The default command will be overridden in docker-compose.yml
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
