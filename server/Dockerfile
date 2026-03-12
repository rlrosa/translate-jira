# Use an official Python runtime as a base image
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Set environment variable to ensure logs are not buffered
ENV PYTHONUNBUFFERED=1

# Copy the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install PyTorch with CUDA support using the specified index URL
# Make sure your CUDA version on the host matches the toolkit version (e.g., 12.1)
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install any other needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY server.py .

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Set a default for the WORKERS environment variable.
# This will be overridden by the 'environment' section in docker-compose.yml
ENV WORKERS 1

# Run the Flask application using Gunicorn.
# We use the "shell form" (no brackets) so that the $WORKERS environment
# variable is correctly expanded by the shell.
# Added flags for unbuffered access and error logging to stdout.
CMD gunicorn -b 0.0.0.0:5000 --workers $WORKERS --timeout 120 --log-level info --access-logfile - --error-logfile - server:app

