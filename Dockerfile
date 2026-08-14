FROM python:3.10-slim

# Ownership marker for the official MCP registry (registry.modelcontextprotocol.io).
LABEL io.modelcontextprotocol.server.name="io.github.erpipe-org/mcp-odoo"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy source code
COPY . /app/

# Create logs directory
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Install the package using the dependency constraints declared by the project.
RUN pip install --no-cache-dir .

# Runtime Odoo connection values should be supplied via `docker run -e ...`.
# Do not bake credential placeholders into the image; Docker flags password ENV
# declarations as secrets even when the default is empty.
ENV ODOO_TIMEOUT="30"
ENV ODOO_VERIFY_SSL="1"
ENV DEBUG="0"

# This hosted deployment is intentionally Odoo Sign-only. These are non-secret
# policy defaults and can still be overridden by the runtime environment.
ENV ODOO_MCP_PLUGINS="sign"
ENV ODOO_MCP_PROFILE="sign"
ENV ODOO_MCP_TOOLS_INCLUDE="sign_*"
ENV ODOO_MCP_SIGN_ENABLE_WRITES="1"
ENV ODOO_MCP_ENABLE_WRITES="0"
ENV ODOO_MCP_MAX_ATTACHMENT_BYTES="16777216"
ENV ODOO_MCP_MAX_ATTACHMENT_UPLOAD_BYTES="16777216"

# Set stdout/stderr to unbuffered mode
ENV PYTHONUNBUFFERED=1

# Streamable HTTP uses this port by default when enabled.
EXPOSE 8000

# Preserve stdio for local Docker use, but route HTTP transports through the
# fail-closed remote entrypoint that requires OAuth or bearer authentication.
ENTRYPOINT ["python", "-m", "odoo_mcp.docker_entrypoint"]
