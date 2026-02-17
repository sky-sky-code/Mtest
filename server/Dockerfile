FROM python:3.12-alpine

WORKDIR /app

COPY pyproject.toml poetry.lock* ./

RUN apk update && pip install --no-cache-dir poetry && poetry config virtualenvs.create && poetry install --no-root

COPY . /app
