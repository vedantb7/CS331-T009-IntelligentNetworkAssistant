FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \ 
    apt-get install -y \ 
        iproute2 \ 
        iputils-ping \
        iperf3 \ 
        net-tools \
        iptables \
        kmod \
        procps \
        docker.io && \
    rm -rf /var/lib/apt/lists/*

CMD ["python3", "-u", "client.py"]