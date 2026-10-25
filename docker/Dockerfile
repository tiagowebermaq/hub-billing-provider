FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd --create-home hub
USER hub

EXPOSE 8000
CMD ["uvicorn", "hub_billing.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
