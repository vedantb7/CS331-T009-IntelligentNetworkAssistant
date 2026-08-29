FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y iputils-ping && \
    rm -rf /var/lib/apt/lists/*

COPY client.py .

CMD ["python3", "-u", "client.py"]