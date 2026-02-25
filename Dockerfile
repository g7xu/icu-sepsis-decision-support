FROM python:3.11-slim

# Install system dependencies (including gcc for some python packages if needed)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install Python dependencies
COPY requirements.txt /code/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . /code/

# Collect static files at build time (whitenoise serves from STATIC_ROOT)
RUN python manage.py collectstatic --noinput

# Expose port 8000
EXPOSE 8000

# Production: gunicorn with --preload (loads demo cache once in master, shared via fork)
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--preload"]
