FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/
COPY .env.example .env.example

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["timeline-sync"]
