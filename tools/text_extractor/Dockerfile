FROM python:3.13.5-slim

LABEL maintainer="Zipstack Inc."
ENV UNSTRACT_ENTRYPOINT "python /app/src/main.py"

# Install dependencies for unstructured library's partition
RUN apt-get update && apt-get --no-install-recommends -y install libmagic-dev poppler-utils\
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -U pip
# Set the working directory in the container
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
# Copy the contents of your project directory into the container at /app
COPY src /app/src/
WORKDIR /app/src


ENTRYPOINT ["python", "main.py"]
