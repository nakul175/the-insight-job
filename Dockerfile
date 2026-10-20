FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build-only-static-collection DJANGO_DEBUG=true python manage.py collectstatic --noinput \
    && useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "30", "--access-logfile", "-"]

# Dedicated non-root browser worker; the host must permit Chromium's sandbox.
FROM base AS browser-worker
USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright
USER appuser
CMD ["python", "manage.py", "browser_handoffs"]

FROM base AS runtime
