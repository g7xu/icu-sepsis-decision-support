FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# System deps (libpq for postgres drivers)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r /code/requirements.txt

COPY app /code/app

EXPOSE 8000

# Dev-friendly default: hot reload enabled (docker-compose mounts the code)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
