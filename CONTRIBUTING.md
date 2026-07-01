# Contributing to tinycode-operator

## Development Setup

1. Install Python 3.11+
2. Install [Helm](https://helm.sh)
3. Clone the repository
4. Install dependencies (from operator directory):
   ```bash
   cd operator
   pip install -r requirements.txt
   ```

## Commands

```bash
# Install CRDs
make install

# Build operator image
make docker-build

# Push operator image
make push

# Deploy operator
helm install tinycode-operator helm-charts/tinycode-operator

# Run operator locally (for development)
cd operator && kopf run --standalone main.py
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes to:
   - `operator/` — Python operator code using kopf
   - `bundle/` — CRD definitions
   - `helm-charts/` — Helm chart templates
   - `config/` — sample configurations
4. Test operator behavior with a local cluster
5. Use conventional commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
6. Push and open a PR against `main`

## Operator Architecture

The operator uses [kopf](https://kopf.readthedocs.io) to watch `TinycodeInstance` custom resources and reconcile them into Kubernetes `Deployment`, `Service`, and `ConfigMap` objects.

## Testing Changes

1. Build operator: `make docker-build`
2. Deploy to test cluster: `helm upgrade --install tinycode-operator helm-charts/tinycode-operator --set image.tag=<your-tag>`
3. Create a test CR:
   ```bash
   kubectl apply -f config/samples/tinycode_v1alpha1_basic.yaml
   ```
4. Verify reconciliation:
   ```bash
   kubectl get deployments,services,configmaps -l app.kubernetes.io/managed-by=tinycode-operator
   kubectl logs -l app.kubernetes.io/name=tinycode-operator
   ```

## CRD Changes

When modifying CRDs in `bundle/`:
1. Update the CRD YAML
2. Run `make install` to apply to your cluster
3. Regenerate the bundle: `make bundle`
4. Test with sample resources in `config/samples/`

## Questions?

Open a [GitHub Issue](https://github.com/bobbyjohnstx/tinycode-operator/issues) for bugs or feature requests.
