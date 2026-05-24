# KorgKode Sandbox — Enterprise Cloud Virtualization
#
# Builds the Docker image with a Jules-compatible environment.
# Preinstalled: Python 3.12, Node 22, Go, Rust, Java 21, Docker CLI, git, etc.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WORKSPACE=/workspace

# System deps
RUN apt-get update && apt-get install -y \
    curl wget git jq ripgrep tmux vim nano \
    build-essential cmake ninja-build \
    docker.io docker-compose \
    sudo lsb-release gnupg software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# Python
RUN apt-get update && apt-get install -y python3.12 python3-pip python3-venv && \
    pip3 install --upgrade pip poetry uv pytest ruff black mypy && \
    rm -rf /var/lib/apt/lists/*

# Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm pnpm yarn eslint prettier && \
    rm -rf /var/lib/apt/lists/*

# Go
RUN curl -fsSL https://go.dev/dl/go1.24.3.linux-amd64.tar.gz | tar -C /usr/local -xz
ENV PATH=$PATH:/usr/local/go/bin

# Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH=$PATH:/root/.cargo/bin

# Java
RUN apt-get update && apt-get install -y openjdk-21-jdk maven gradle && \
    rm -rf /var/lib/apt/lists/*

# Chrome/Chromium for Playwright
RUN apt-get update && apt-get install -y chromium-browser && \
    rm -rf /var/lib/apt/lists/*

# Create workspace
RUN mkdir -p $WORKSPACE
WORKDIR $WORKSPACE

# Default user
RUN useradd -m -s /bin/bash kodex && \
    echo "kodex ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
USER kodex

CMD ["/bin/bash"]