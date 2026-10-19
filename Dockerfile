FROM python:3.12-slim
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
