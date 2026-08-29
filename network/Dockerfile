FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \ 
    apt-get install -y \ 
        iproute2 \ 
        iputils-ping \ 
        net-tools && \ 
    rm -rf /var/lib/apt/lists/*

COPY client.py .

CMD ["python3", "-u", "client.py"]