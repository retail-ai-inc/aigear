# GCP Cost Estimation

> Region: **asia-northeast1 (Tokyo)**. Prices are estimates based on GCP public pricing and may vary. Always verify current rates at [cloud.google.com/pricing](https://cloud.google.com/pricing).

---

## Aigear vs Other MLOps Tools

### Tools Compared

| Tool | Type | Key additional infrastructure vs Aigear |
|---|---|---|
| **Aigear** | Lightweight orchestration framework | — (baseline) |
| **Aigear + MLflow** | Aigear + experiment tracking | MLflow tracking server (Cloud Run) + Cloud SQL |
| **Kubeflow on GKE** | Kubernetes-native ML platform | Dedicated GKE cluster for control plane (always-on) |
| **Vertex AI** | Fully managed GCP MLOps platform | Vertex AI Endpoints (1 dedicated endpoint per model) |

### Additional Infrastructure per Tool

**Aigear + self-hosted MLflow**

| Component | Spec | Monthly cost |
|---|---|---|
| MLflow tracking server | Cloud Run (min instances = 0) | ~$5 |
| Cloud SQL (metadata DB) | PostgreSQL db-f1-micro (shared core) | ~$7 |
| **Additional total** | | **~$12 / month** |

**Kubeflow on GKE**

Kubeflow requires a dedicated GKE cluster running at all times to host its control plane: Pipelines backend, Pipelines UI, Metadata server, Artifact store, and KServe for model serving. This is a fixed cost regardless of pipeline usage.

| Component | Spec | Monthly cost |
|---|---|---|
| GKE control plane cluster | 3× e2-standard-4 (4 vCPU / 16 GB) | $289 |
| **Platform overhead** | | **$289 / month (fixed)** |

> Training steps run as pods on an auto-scaling GPU node pool (pay per use). Model serving runs via KServe on the same cluster — no separate serving cost beyond the cluster itself.

**Vertex AI**

Vertex AI is fully managed with no cluster to maintain. However, Online Prediction Endpoints require a **dedicated always-on node per model endpoint**.

| Component | Spec | Cost per endpoint |
|---|---|---|
| Vertex AI Online Endpoint | n1-standard-2 (min 1 node, 24/7) | ~$108 / month |

Training via Vertex AI Custom Jobs uses the same underlying GCE compute as Aigear's ephemeral VMs (comparable price, ~10% management premium).

### Cost Comparison by Scenario

**Scenario A — Small Team (3 models, 6 pipeline versions, 9 runs / month)**

| | Aigear | Aigear + MLflow | Kubeflow | Vertex AI |
|---|---|---|---|---|
| Training VMs | $1.27 | $1.27 | $1.27 | $1.40 |
| Model serving | $48 (1 GKE node, shared) | $48 | included in cluster | $324 (3 endpoints × $108) |
| Storage & other | $2.36 | $2.36 | $2.36 | $2.36 |
| Platform overhead | $0.66 | $0.66 + $12 | $289 | $0 |
| **Total** | **~$52** | **~$64** | **~$293** | **~$328** |

**Scenario B — Medium Team (8 models, 18 pipeline versions, 50 runs / month)**

| | Aigear | Aigear + MLflow | Kubeflow | Vertex AI |
|---|---|---|---|---|
| Training VMs | $36.56 | $36.56 | $36.56 | $40.22 |
| Model serving | $96 (2 GKE nodes, shared) | $96 | included in cluster | $864 (8 endpoints × $108) |
| Storage & other | $9.78 | $9.78 | $9.78 | $9.78 |
| Platform overhead | $2.58 | $2.58 + $12 | $289 | $0 |
| **Total** | **~$143** | **~$155** | **~$335** | **~$914** |

**Scenario C — Large Team (20 models, 60 pipeline versions, 130 runs / month)**

| | Aigear | Aigear + MLflow | Kubeflow | Vertex AI |
|---|---|---|---|---|
| Training VMs | $187.33 | $187.33 | $187.33 | $206.06 |
| Model serving | $145 (3 GKE nodes, shared) | $145 | included in cluster | $2,160 (20 endpoints × $108) |
| Storage & other | $31.65 | $31.65 | $31.65 | $31.65 |
| Platform overhead | $9.30 | $9.30 + $12 | $289 | $0 |
| **Total** | **~$364** | **~$376** | **~$508** | **~$2,398** |

### Summary

| Scenario | Aigear | Aigear + MLflow | Kubeflow | Vertex AI |
|---|---|---|---|---|
| A: Small team (3 models) | **~$52** | ~$64 | ~$293 | ~$328 |
| B: Medium team (8 models) | **~$143** | ~$155 | ~$335 | ~$914 |
| C: Large team (20 models) | **~$364** | ~$376 | ~$508 | ~$2,398 |

### Key Cost Drivers by Tool

**Kubeflow — fixed platform overhead**

The $289/month cluster cost is constant regardless of how many pipelines you run. This makes Kubeflow expensive for small teams and cost-competitive only when the cluster is heavily utilized.

**Vertex AI — serving cost scales linearly with model count**

Vertex AI Endpoints charge per dedicated node per model. For 3 models the premium over Aigear is 6×. For 20 models it is 6.6×. Teams with many small models pay a significant penalty.

**Aigear + MLflow — negligible overhead**

Adding MLflow for experiment tracking and model registry costs only ~$12/month. This is the lowest-cost path to a complete MLOps stack with versioned experiments.

### When Each Tool Makes Economic Sense

| Tool | Best fit | Avoid when |
|---|---|---|
| **Aigear** | Many models, cost-sensitive teams, GCP-native | Need built-in experiment tracking today |
| **Aigear + MLflow** | Same as above + need experiment tracking | Need fully managed infrastructure |
| **Kubeflow** | Large teams with heavy continuous workloads that justify the cluster cost | Small teams or low run frequency — idle cluster is expensive |
| **Vertex AI** | 1–3 models, team wants fully managed with zero infra ops | Many models — serving cost explodes linearly |

---

## Pricing Reference

| Resource | Spec | Free Tier | Unit Price |
|---|---|---|---|
| e2-medium (CPU VM) | 2 vCPU / 4 GB | — | $0.042 / hour |
| n1-standard-4 + T4 (GPU VM) | 4 vCPU / 15 GB + NVIDIA T4 | — | $0.647 / hour |
| e2-standard-2 (GKE node) | 2 vCPU / 8 GB | — | $0.067 / hour |
| GKE cluster management | Zonal cluster | **First zonal cluster free** | $0.10 / cluster / hour |
| GCS Standard | — | 5 GB storage / 50,000 Class A ops / month | $0.023 / GB / month |
| Artifact Registry | — | 0.5 GB / month | $0.10 / GB / month |
| Cloud Scheduler | — | **First 3 jobs free** | $0.10 / job / month |
| Cloud KMS | — | 200,000 ops / key / month | $0.06 / key version / month |
| Pub/Sub | — | **10 GB / month** | $0.04 / GB |
| Cloud Functions (Gen2) | — | **2M invocations / 400K GB·s / month** | $0.40 / million invocations |
| Firestore (DAG fan-in counter) | — | **20,000 writes / 50,000 reads / day** | $0.18 / 100K writes |

> **Pub/Sub, Cloud Functions, and Firestore fall within the free tier in all scenarios below.**

---

## Scenario Assumptions

Each model typically maintains multiple active pipeline versions simultaneously:

| Version Type | Count | Run Frequency | Purpose |
|---|---|---|---|
| Production | 1 per model | Weekly / bi-weekly | Currently serving, retrained on schedule |
| Challenger | 1–2 per model | Half of production frequency | New algorithm or feature set under validation |
| Experimental | 1–3 per model | On-demand / monthly | Research exploration, not yet production-bound |

Total pipeline versions = **models × avg versions per model**. Each version has its own Cloud Scheduler job, KMS key, and independent run schedule.

---

## Scenario A — Small Team

**Profile:**
- **3 models × 2 versions** (1 prod + 1 challenger) = **6 pipeline versions**
- Production (3 versions): 2 runs / month each → 6 runs
- Challenger (3 versions): 1 run / month each → 3 runs
- **Total: 9 runs / month**
- Steps: `fetch_data` (20 min, CPU) → `[preprocessing ‖ feature_eng]` (30 min each, parallel) → `training` (2 h, CPU) → `model_service`
- GKE: 1 node running 24/7 for model serving

| Component | Calculation | Monthly Cost |
|---|---|---|
| fetch_data VM | $0.042 × (20/60) h × 9 runs | $0.13 |
| preprocessing VM | $0.042 × (30/60) h × 9 runs | $0.19 |
| feature_eng VM (parallel) | $0.042 × (30/60) h × 9 runs | $0.19 |
| training VM (2 h, CPU) | $0.042 × 2 h × 9 runs | $0.76 |
| GKE node (e2-standard-2 × 1) | $0.067 × 24 h × 30 days | $48.24 |
| GCS (50 GB) | $0.023 × 50 | $1.15 |
| Artifact Registry (6 GB) | $0.10 × 5.5 (minus 0.5 GB free) | $0.55 |
| Cloud KMS (6 keys) | $0.06 × 6 | $0.36 |
| Cloud Scheduler (3 paid jobs) | $0.10 × 3 | $0.30 |
| Pub/Sub / Cloud Functions / Firestore | within free tier | $0.00 |
| **Total** | | **~$52 / month** |

---

## Scenario B — Medium Team

**Profile:**
- **8 models × ~2.25 versions** (8 prod + 8 challenger + 2 experimental) = **18 pipeline versions**
- Production (8 versions): 4 runs / month each → 32 runs
- Challenger (8 versions): 2 runs / month each → 16 runs
- Experimental (2 versions): 1 run / month each → 2 runs
- **Total: 50 runs / month**
- Steps: `fetch_data` (30 min, CPU) → `[preprocessing ‖ feature_eng]` (45 min each, parallel) → `training` (1 h, GPU) → `model_service`
- GKE: 2 nodes running 24/7 for model serving

| Component | Calculation | Monthly Cost |
|---|---|---|
| fetch_data VM | $0.042 × 0.5 h × 50 runs | $1.05 |
| preprocessing VM | $0.042 × 0.75 h × 50 runs | $1.58 |
| feature_eng VM (parallel) | $0.042 × 0.75 h × 50 runs | $1.58 |
| training VM (1 h, GPU) | $0.647 × 1 h × 50 runs | $32.35 |
| GKE nodes (e2-standard-2 × 2) | $0.067 × 2 × 24 h × 30 days | $96.48 |
| GCS (250 GB) | $0.023 × 250 | $5.75 |
| Artifact Registry (15 GB) | $0.10 × 14.5 | $1.45 |
| Cloud KMS (18 keys) | $0.06 × 18 | $1.08 |
| Cloud Scheduler (15 paid jobs) | $0.10 × 15 | $1.50 |
| Pub/Sub / Cloud Functions / Firestore | within free tier | $0.00 |
| **Total** | | **~$143 / month** |

---

## Scenario C — Large Team

**Profile:**
- **20 models × 3 versions** (1 prod + 1 challenger + 1 experimental) = **60 pipeline versions**
- Production (20 versions): 4 runs / month each → 80 runs
- Challenger (20 versions): 2 runs / month each → 40 runs
- Experimental (20 versions): 0.5 runs / month each → 10 runs
- **Total: 130 runs / month**
- Steps: `fetch_data` (30 min, CPU) → `[preprocessing ‖ feature_eng ‖ data_validation]` (60 / 60 / 30 min, parallel) → `training` (2 h, GPU) → `evaluation` (30 min, CPU) → `model_service`
- GKE: 3 nodes running 24/7 for model serving

| Component | Calculation | Monthly Cost |
|---|---|---|
| fetch_data VM | $0.042 × 0.5 h × 130 runs | $2.73 |
| preprocessing VM | $0.042 × 1 h × 130 runs | $5.46 |
| feature_eng VM (parallel) | $0.042 × 1 h × 130 runs | $5.46 |
| data_validation VM (parallel) | $0.042 × 0.5 h × 130 runs | $2.73 |
| training VM (2 h, GPU) | $0.647 × 2 h × 130 runs | $168.22 |
| evaluation VM | $0.042 × 0.5 h × 130 runs | $2.73 |
| GKE nodes (e2-standard-2 × 3) | $0.067 × 3 × 24 h × 30 days | $144.72 |
| GCS (800 GB) | $0.023 × 800 | $18.40 |
| Artifact Registry (40 GB) | $0.10 × 39.5 | $3.95 |
| Cloud KMS (60 keys) | $0.06 × 60 | $3.60 |
| Cloud Scheduler (57 paid jobs) | $0.10 × 57 | $5.70 |
| Pub/Sub / Cloud Functions / Firestore | within free tier | $0.00 |
| **Total** | | **~$364 / month** |

---

## Aigear Cost Summary

| Scenario | Models | Versions | Total Pipelines | Runs / Month | VM Cost | GKE Cost | Storage & Other | **Total** |
|---|---|---|---|---|---|---|---|---|
| A: Small team | 3 | 2 | 6 | 9 | $1.27 | $48.24 | $2.36 | **~$52 / month** |
| B: Medium team | 8 | ~2.25 | 18 | 50 | $36.56 | $96.48 | $9.78 | **~$143 / month** |
| C: Large team | 20 | 3 | 60 | 130 | $187.33 | $144.72 | $31.65 | **~$364 / month** |

**Key observations:**
- **Scenario A:** GKE idle cost ($48) accounts for 93% of the bill at this run frequency. Scaling the node pool to 0 outside business hours cuts the total significantly.
- **Scenarios B and C:** GPU training dominates VM spend (89% and 90% respectively). Spot VMs can reduce this by 60–80%.
- **Cloud Scheduler scales with pipeline versions.** 60 versions means 57 paid Scheduler jobs ($5.70/month) — a fixed cost that grows with version count.

### Impact of Parallel DAG

Parallel execution does **not increase cost** — it reduces wall-clock time while keeping total VM-hours the same.

| Mode | Total VM·hours per run | Wait time per run | Cost per run |
|---|---|---|---|
| Sequential (45 min + 45 min) | 1.5 VM·h | 90 min | $0.063 |
| **Parallel (45 min ‖ 45 min)** | 1.5 VM·h | **45 min** | **$0.063** |

The only new cost introduced by parallel DAG support is **Firestore**, which falls entirely within the free tier for all three scenarios.

| Scenario | Firestore writes / month | Free tier limit | Cost |
|---|---|---|---|
| A | ~45 | 600,000 / month | **$0.00** |
| B | ~250 | 600,000 / month | **$0.00** |
| C | ~910 | 600,000 / month | **$0.00** |

---

## Cost Optimization Tips

| Tip | Configuration | Applicable to | Potential saving |
|---|---|---|---|
| **GPU training + CPU inference** — use GPU spec for training steps, set `"gpu": false` + `"spec": "e2-medium"` in `model_service.resources` | `env.json` → `pipelines.<version>.model_service.resources` | Scenarios B, C | $417–$1,250 / month on serving VMs |
| Use **Spot VMs** for training steps | VM provisioning config | All scenarios | 60–80% off VM cost |
| Scale GKE node pool to 0 outside business hours | GKE node pool config | Scenario A (low run frequency) | Up to 50% off GKE cost |
| Use **Committed Use Discounts** for GKE nodes (1-year) | GCP billing | All scenarios | ~37% off GKE node cost |
| Reduce GPU training time with mixed-precision (`torch.amp`) | Training step code | Scenarios B, C | Proportional to time saved |
| Store intermediate artifacts in **Nearline** instead of Standard GCS | GCS bucket config | Large artifact stores | ~40% off storage cost |
| Consolidate experimental versions to run only on-demand | `env.json` scheduler config | All scenarios | Reduces Scheduler job count and run frequency |
