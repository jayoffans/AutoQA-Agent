FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright AUTOQA_ENV=production HOST=0.0.0.0
WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY autoqa ./autoqa
RUN pip install -r requirements.lock && pip install --no-deps . \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 autoqa \
    && mkdir -p /app/data /app/artifacts && chown -R autoqa:autoqa /app /ms-playwright
USER autoqa
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8765')+'/health')"
CMD ["python", "-m", "autoqa"]
