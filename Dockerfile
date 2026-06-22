# Multi-stage operator container image.
# Stage 1: build — install Python deps
FROM registry.access.redhat.com/ubi9/python-311:latest AS builder

USER root
WORKDIR /build
COPY operator/requirements.txt .
RUN pip install --no-cache-dir --target /install -r requirements.txt

# Stage 2: runtime — minimal image with helm + operator
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest

# Install helm
ARG HELM_VERSION=v3.17.3
RUN microdnf install -y tar gzip shadow-utils python3.11 && \
    curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
      -o /tmp/helm.tar.gz && \
    tar -xzf /tmp/helm.tar.gz -C /tmp && \
    install -m 755 /tmp/linux-amd64/helm /usr/local/bin/helm && \
    rm -rf /tmp/helm* && \
    microdnf remove -y tar gzip shadow-utils && \
    microdnf clean all

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /install /app/deps

# Copy operator source
COPY operator/ /app/

# Copy Helm chart
COPY helm-charts/ /helm-charts/

RUN chown -R 1001:0 /app /helm-charts && chmod -R g=u /app /helm-charts

ENV PYTHONPATH=/app/deps
ENV HELM_CHART_PATH=/helm-charts/tinycode
ENV HOME=/tmp

# UID 1001 already exists in ubi-minimal (operator user)
USER 1001:0

ENTRYPOINT ["python3.11", "-m", "kopf", "run", "--all-namespaces", "--liveness=http://0.0.0.0:8081/healthz", "/app/main.py"]
