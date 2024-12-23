# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables to prevent python from writing .pyc files and ensure output is logged to the console
ENV PYTHONUNBUFFERED 1
ENV PIP_NO_CACHE_DIR 1

# Set the working directory to /app inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt /app/

# Install the dependencies specified in the requirements.txt file
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy the current directory contents into the container
COPY . /app/

# Expose the port that Gunicorn will run on (typically 8000)
EXPOSE 8000

# Command to run the application with Gunicorn (replace 'myproject' with your project name)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "api.wsgi:application"]
