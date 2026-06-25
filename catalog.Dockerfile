FROM quay.io/operator-framework/opm:latest AS builder

# Copy FBC catalog
COPY catalog /catalog

# Validate the catalog
RUN opm validate /catalog

# Serve the catalog via FBC
FROM quay.io/operator-framework/opm:latest
COPY --from=builder /catalog /catalog
ENTRYPOINT ["/bin/opm", "serve", "/catalog"]
