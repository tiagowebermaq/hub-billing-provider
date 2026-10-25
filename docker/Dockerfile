# Estágio base: dependências de runtime
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Estágio de testes: docker build --target test  (ou docker compose run --rm tests)
FROM base AS test
RUN pip install -e ".[dev]"
COPY tests ./tests
COPY tools ./tools
CMD ["sh", "-c", "ruff check src tests tools && mypy src && pytest --cov --cov-report=term-missing --cov-fail-under=80"]

# Estágio final: imagem de produção
FROM base AS runtime
RUN pip install . && useradd --create-home hub
USER hub
EXPOSE 8000
CMD ["uvicorn", "hub_billing.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
