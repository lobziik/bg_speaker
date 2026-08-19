# =============================================================================
# Stage 1: Build the Python virtualenv
#
# Built on the same UBI10 base as the runtime stage so the venv's interpreter
# symlink (/usr/bin/python3, 3.12) resolves identically in both.
# =============================================================================
FROM registry.access.redhat.com/ubi10-init:latest AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_PYTHON=/usr/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy

# Dependencies only - the application itself is copied into the runtime stage,
# matching how `python -m src.main` is invoked there.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# =============================================================================
# Stage 2: Runtime image - systemd supervising Caddy + the narrator app
# =============================================================================
FROM registry.access.redhat.com/ubi10-init:latest

LABEL org.opencontainers.image.title="bg-narrator" \
      org.opencontainers.image.description="BG3 Twitch Narrator Bot - Channel Points TTS narrator" \
      org.opencontainers.image.licenses="MIT"

# gettext provides envsubst, used to render the Caddyfile at first boot.
# Piper ships its own espeak-ng bridge and data inside the wheel, so no
# system speech packages are needed.
RUN dnf install -y \
        gettext \
        curl \
        && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# Caddy terminates TLS in front of the app. Taking the binary from the official
# image keeps the architecture correct on both amd64 and arm64 builds.
COPY --from=docker.io/library/caddy:2 /usr/bin/caddy /usr/local/bin/caddy

# Unprivileged account for the application service
RUN useradd --system --create-home --home-dir /var/lib/narrator --shell /sbin/nologin narrator

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY config ./config
COPY migrations ./migrations
COPY overlay ./overlay

COPY container/systemd/*.service /etc/systemd/system/
COPY container/scripts/ /usr/local/bin/
COPY container/templates/Caddyfile.template /etc/caddy/

# COPY preserves the host's file modes, which are not guaranteed to be
# world-readable; the app service runs as `narrator` and must be able to read
# its own code. Normalise instead of trusting the build host's umask.
RUN chmod -R a+rX /app/src /app/config /app/migrations /app/overlay && \
    chmod +x /usr/local/bin/narrator-*.sh && \
    mkdir -p /etc/narrator /var/lib/narrator/data /var/lib/caddy && \
    chown -R narrator:narrator /var/lib/narrator

RUN systemctl enable \
        narrator-init.service \
        narrator-app.service \
        narrator-caddy.service

# 80: HTTP (ACME challenge + redirect to HTTPS)
# 443: HTTPS dashboard, OBS overlay and the overlay WebSocket
EXPOSE 80/tcp 443/tcp

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -sf http://127.0.0.1:8000/health || exit 1

# SQLite database and cached Piper voice models
VOLUME ["/var/lib/narrator"]
# Let's Encrypt certificates and Caddy state
VOLUME ["/var/lib/caddy"]

CMD ["/sbin/init"]
