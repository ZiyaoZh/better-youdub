FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CODEX_NON_INTERACTIVE=1
ENV PATH="/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    docker.io \
    fd-find \
    git \
    git-lfs \
    gnupg \
    gosu \
    jq \
    less \
    make \
    nano \
    openssh-client \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    ripgrep \
    sudo \
    unzip \
    vim-tiny \
    xz-utils \
  && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
  && apt-get update \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

RUN install -d -m 0755 /etc/apt/keyrings \
  && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
    -o /etc/apt/keyrings/claude-code.asc \
  && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
    > /etc/apt/sources.list.d/claude-code.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends claude-code \
  && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex

RUN ln -sf /usr/bin/fdfind /usr/local/bin/fd

COPY codex-entrypoint.sh /usr/local/bin/codex-entrypoint
RUN chmod +x /usr/local/bin/codex-entrypoint

WORKDIR /workspace/repo

ENTRYPOINT ["codex-entrypoint"]
CMD ["codex"]
