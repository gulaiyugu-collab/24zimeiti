FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app

COPY --chown=app:app app ./app
COPY --chown=app:app static ./static
RUN mkdir -p /app/var/acquisition \
    && chown -R app:app /app/var

USER app

EXPOSE 8787

CMD ["sh", "-c", "test -n \"$PROJECT024_ACCESS_PASSWORD\" || { echo 'PROJECT024_ACCESS_PASSWORD is required'; exit 1; }; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8787}"]
