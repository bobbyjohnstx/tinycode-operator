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
    ARCH=$(uname -m) && \
    case "$ARCH" in x86_64) HELM_ARCH=amd64 ;; aarch64) HELM_ARCH=arm64 ;; *) HELM_ARCH=amd64 ;; esac && \
    HELM_FILE="helm-${HELM_VERSION}-linux-${HELM_ARCH}.tar.gz" && \
    curl -fsSL "https://get.helm.sh/${HELM_FILE}" -o /tmp/${HELM_FILE} && \
    curl -fsSL "https://get.helm.sh/${HELM_FILE}.sha256sum" -o /tmp/${HELM_FILE}.sha256sum && \
    cd /tmp && sha256sum -c ${HELM_FILE}.sha256sum && \
    tar -xzf /tmp/${HELM_FILE} -C /tmp && \
    install -m 755 /tmp/linux-${HELM_ARCH}/helm /usr/local/bin/helm && \
    rm -rf /tmp/helm* /tmp/linux-${HELM_ARCH} && \
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
ENV HELM_DRIVER=configmap

# UID 1001 already exists in ubi-minimal (operator user)
USER 1001:0

ENTRYPOINT ["python3.11", "-m", "kopf", "run", "--all-namespaces", "--liveness=http://0.0.0.0:8081/healthz", "/app/main.py"]
