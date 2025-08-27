FROM python:3.12-slim

# Prevents Python from writing .pyc files and buffers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Install basic networking tools for downloading
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl wget bash dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
ARG USER=appuser
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} ${USER} \
    && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USER}

# Ensure pip --user installs are on PATH for non-root user
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Workspace directory (bind-mounted via docker compose)
WORKDIR /workspace
RUN chown -R ${UID}:${GID} /workspace

# Install Python dependencies, if any
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && if [ -s /tmp/requirements.txt ]; then pip install -r /tmp/requirements.txt; fi \
    && rm -f /tmp/requirements.txt

# Entrypoint that either runs the provided command or stays alive
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
# Normalize line endings just in case and ensure executable
RUN dos2unix /usr/local/bin/entrypoint.sh || sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Drop privileges
USER ${USER}

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
# Default to an interactive shell if no command provided
CMD ["bash"]
