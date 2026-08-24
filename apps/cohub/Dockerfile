FROM node:22-alpine AS frontend

WORKDIR /frontend
COPY package.json package-lock.json tsconfig.json vite.config.ts ./
COPY frontend ./frontend
COPY cohub/static ./cohub/static
RUN npm ci && npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app
COPY --from=frontend /frontend/cohub/static /app/cohub/static
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 cohub \
    && mkdir -p /data \
    && chown -R cohub:cohub /data

USER cohub
EXPOSE 8765

ENTRYPOINT ["cohub"]
CMD ["--data-dir", "/data", "serve", "--host", "0.0.0.0", "--port", "8765", "--executor", "hermes"]
