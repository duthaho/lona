FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 cohub \
    && mkdir -p /data \
    && chown -R cohub:cohub /data

USER cohub
EXPOSE 8765

ENTRYPOINT ["cohub"]
CMD ["--data-dir", "/data", "serve", "--host", "0.0.0.0", "--port", "8765", "--executor", "hermes"]
