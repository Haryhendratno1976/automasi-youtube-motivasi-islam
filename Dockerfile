FROM python:3.11-slim

# Install system dependencies (ImageMagick, FFmpeg, build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    imagemagick \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Fix ImageMagick policy for MoviePy
RUN sed -i 's/none/read,write/g' /etc/ImageMagick-6/policy.xml || true

WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Expose ports for Flask web dashboard
EXPOSE 5000

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Command to run (can be overridden by Docker Compose or Railway)
CMD ["python", "app.py"]
