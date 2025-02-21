# Use the official Playwright Python image pinned to a specific version
FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

# Create a directory for your app
WORKDIR /app

# Copy in your requirements.txt and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose port 8000 (or whatever port your FastAPI listens on)
EXPOSE 8000

# Command to start your FastAPI application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]