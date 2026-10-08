FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user instead of root
RUN useradd --system --no-create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p instance \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# python:slim has no curl, so use the Python standard library
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/', timeout=2)"]

CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:5000", "--timeout", "120", "--access-logfile", "-", "app:app"]
