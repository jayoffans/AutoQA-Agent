FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY autoqa ./autoqa
RUN pip install -r requirements.lock && pip install --no-deps . \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 autoqa \
    && mkdir -p /app/data /app/artifacts && chown -R autoqa:autoqa /app /ms-playwright
USER autoqa
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"
CMD ["uvicorn", "autoqa.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
