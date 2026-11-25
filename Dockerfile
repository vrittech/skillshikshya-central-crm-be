# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies
# Pango and fonts are for WeasyPrint (invoice and receipt PDFs).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project
COPY . /app/

# Expose port
EXPOSE 8188

# Default command
CMD ["gunicorn", "--bind", "0.0.0.0:8188", "skillshikshyacrm.wsgi:application"]
