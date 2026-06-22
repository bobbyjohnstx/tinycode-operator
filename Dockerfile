# Multi-stage operator container image.
# Stage 1: build — install Python deps
FROM registry.access.redhat.com/ubi9/python-311:latest AS builder

WORKDIR /build
COPY operator/requirements.txt .
RUN pip install --no-cache-dir --target /install -r requirements.txt

# Stage 2: runtime — minimal image with helm + operator
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

# Install helm
ARG HELM_VERSION=v3.17.3
RUN microdnf install -y curl tar gzip && \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
      -o /tmp/helm.tar.gz && \
    tar -xzf /tmp/helm.tar.gz -C /tmp && \
    install -m 755 /tmp/linux-amd64/helm /usr/local/bin/helm && \
    rm -rf /tmp/helm* && \
    microdnf remove -y curl tar gzip && \
    microdnf clean all

# Install Python 3.11
RUN microdnf install -y python3.11 && microdnf clean all

# Non-root user — matches the restricted SCC UID
RUN useradd -u 1000 -r -g 0 -s /sbin/nologin operator

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /install /app/deps

# Copy operator source
COPY operator/ /app/

# Copy Helm chart
COPY helm-charts/ /helm-charts/

ENV PYTHONPATH=/app/deps
ENV HELM_CHART_PATH=/helm-charts/tinycode
ENV HOME=/tmp

USER 1000

ENTRYPOINT ["python3.11", "/app/main.py"]
