# use a minimal deb-based python image
FROM python:3.12-slim 

# set working directory
WORKDIR /app

# install the required utilities
RUN apt-get update && \ 
    apt-get install -y \ 
        iproute2 \ 
        iputils-ping \
        iperf3 \ 
        net-tools \
        iptables \
        nftables \
        kmod \
        procps \
        docker.io && \
    rm -rf /var/lib/apt/lists/*

# start client application
CMD ["python3", "-u", "client.py"]

# DockerFile follows caching well.