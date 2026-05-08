# GNI — Kubernetes Deployment

Production-grade Kubernetes manifests for the GNI pipeline. Kustomize-based, no Helm.

---

## Architecture Overview

The stack runs as two workload types plus external dependencies:

| Component | Role | Exposure |
|-----------|------|----------|
| **API** | FastAPI HTTP service, port 8000. Health, review, monitoring, WhatsApp bridge. | ClusterIP + Ingress |
| **Worker** | Background pipeline: scoring → LLM draft → render → publish. No HTTP surface. | Internal only |
| **Postgres** | External. Managed DB or separate cluster. `DATABASE_URL` in Secret. | Outbound only |
| **Redis** | External. Cache, rate limiting, dedupe. `REDIS_URL` in Secret. | Outbound only |

Ollama (LLM) can be in-cluster or external; override `OLLAMA_BASE_URL` in the ConfigMap or overlay.

---

## Why Kustomize Over Helm

- **Declarative and simple** — Plain YAML + patches. No templating or packaging layer.
- **GitOps-friendly** — Base + overlays map cleanly to branches/environments. Easy to review diffs.
- **Standard tooling** — `kubectl apply -k` works out of the box. No chart store or Tiller.
- **Low overhead** — No chart versioning or release management. Overlays are just patches.

Helm is useful when you need parameterization, chart reuse, or rollback. For this stack, overlays give enough variation without the extra abstraction.

---

## ConfigMap vs Secret Separation

| Store | Contents | Rationale |
|-------|----------|-----------|
| **ConfigMap** (`gni-config`) | Non-sensitive config: `PYTHONPATH`, `OLLAMA_BASE_URL`, `ENV`, etc. | Safe to commit, easy to diff. Overlays add `ENV` (dev/prod). |
| **Secret** (`gni-secrets`) | Credentials: `DATABASE_URL`, `REDIS_URL`, `API_KEY`, `JWT_SECRET` | Never commit. Use `kubectl create secret`, Sealed Secrets, or External Secrets. |

Both are mounted via `envFrom` so apps receive config as environment variables. Secret creation is manual; see `secret.example.yaml` for keys and create `gni-secrets` before apply.

---

## HPA Behavior

The API HorizontalPodAutoscaler targets CPU and memory:

- **Metrics**: CPU 70%, memory 80%
- **Scale-up**: No stabilization, up to 100% of pods or +2 per 30s
- **Scale-down**: 300s stabilization, at most 50% reduction per 60s

Scale-up is responsive; scale-down is slow to avoid thrashing after traffic spikes. Dev overlay disables HPA by setting `minReplicas=maxReplicas=1`. Prod keeps HPA enabled with `minReplicas=3`.

---

## NetworkPolicy Rationale

Three policies enforce least-privilege networking:

1. **Default deny ingress** — All pods in `gni` reject ingress unless explicitly allowed.
2. **API ingress** — Traffic to `gni-api` allowed from:
   - Same namespace (service-to-service)
   - Ingress controller pods (`ingress-nginx`, `app.kubernetes.io/name=ingress-nginx`)
3. **Egress** — Allow DNS (53) for name resolution; Postgres (5432), Redis (6379), HTTP (80), HTTPS (443) for external services.

This limits lateral movement and ensures workloads only reach intended destinations.

---

## Deployment

**Dev**

```bash
kubectl apply -k k8s/overlays/dev
```

**Prod**

```bash
kubectl apply -k k8s/overlays/prod
```

Ensure `gni-secrets` exists and images are available in the registry before apply.

---

## Verification Checklist

```bash
# Pods running
kubectl get pods -n gni

# HPA status and current metrics
kubectl describe hpa -n gni

# API logs
kubectl logs deploy/gni-api -n gni -f

# Worker logs
kubectl logs deploy/gni-worker -n gni -f

# Ingress and services
kubectl get ingress,svc -n gni
```

Expect `gni-api` pods Ready, `gni-worker` Running, HPA reporting current utilization.

---

## Scaling Test

1. Apply prod overlay.
2. Generate load on the API (e.g. `hey`, `ab`, or repeated `curl`).
3. Watch scaling:

```bash
kubectl get hpa -n gni -w
kubectl get pods -n gni -w
```

4. Stop load; observe slow scale-down (stabilization 300s).

---

## Failure Simulation Test

**API pod failure**

```bash
kubectl delete pod -n gni -l app=gni-api
kubectl get pods -n gni -w   # Replacement pod should start
```

**Worker pod failure**

```bash
kubectl delete pod -n gni -l app=gni-worker
kubectl get pods -n gni -w   # Single replica restarts
```

**Node drain (PDB)**

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir
# PDB (minAvailable: 1) blocks eviction until replacement is Ready
```

These checks confirm self-healing and disruption handling.
