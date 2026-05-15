# GCP Cost Estimation (Quick View)

**Region:** asia-northeast1 (Tokyo). Numbers are **rough estimates** from public GCP pricing — confirm at [cloud.google.com/pricing](https://cloud.google.com/pricing).

---

## Monthly total (USD)

Same three team sizes; **Aigear baseline** = ephemeral training VMs + shared GKE for serving + storage/scheduler/KMS, etc.

| Team size | Aigear | + MLflow (~$12) | Kubeflow (GKE control plane) | Vertex AI (per-model endpoints) |
|-----------|--------|-----------------|------------------------------|----------------------------------|
| Small (3 models, low runs) | **~$52** | ~$64 | ~$293 | ~$328 |
| Medium (8 models, more GPU) | **~$143** | ~$155 | ~$335 | ~$914 |
| Large (20 models, heavy GPU) | **~$364** | ~$376 | ~$508 | ~$2,398 |

**Why the spread**

| Stack | What you pay extra for |
|-------|-------------------------|
| **Aigear** | Mostly **always-on GKE** for serving; training is pay-per-use. |
| **+ MLflow** | ~**$12/mo** (Cloud Run tracking + small Cloud SQL). |
| **Kubeflow** | **~$289/mo fixed** cluster for the platform, even when idle. |
| **Vertex AI** | **~$108/mo per online endpoint** (dedicated node) — cost **grows with model count**. |

---

## Assumptions (one line each)

- **Small:** few models/versions, few runs/month, 1 GKE node, mostly CPU training.  
- **Medium / large:** more versions & runs, more storage; large scenarios assume **GPU training** (main driver of VM cost).  
- Pub/Sub, Cloud Functions, Firestore: **within free tier** in these examples.

---

## Takeaways

1. **Aigear** is cheapest when you have **many models** on **shared serving** — you avoid per-endpoint platform tax.  
2. **Kubeflow** only makes sense when a **big always-on cluster** is justified.  
3. **Vertex AI** is simple to operate but **serving cost scales linearly** with endpoints.  
4. **MLflow add-on** is a small fixed bump for tracking/registry.

**Parallel DAG:** parallel steps do **not** increase VM cost (same VM·hours); they shorten wall time. Firestore fan-in stays in free tier at these scales.

---

## Ideas to spend less

- **GPU for training, CPU for inference** in `model_service.resources` where possible.  
- **Spot / preemptible** for training VMs.  
- **Scale GKE to zero** (or down) when serving can tolerate it — big win on small teams.  
- **Committed use** on steady GKE nodes; **mixed precision** to shorten GPU time; **Nearline GCS** if cold artifacts are OK.

---

## Reference unit prices (Tokyo, indicative)

| Item | Ballpark |
|------|----------|
| e2-medium (CPU) | ~$0.042 / h |
| n1-standard-4 + T4 (GPU) | ~$0.647 / h |
| e2-standard-2 (GKE node) | ~$0.067 / h |
| GKE management | first zonal cluster often free; then ~$0.10 / cluster / h |
| GCS Standard | ~$0.023 / GB / mo |
| Artifact Registry | ~$0.10 / GB / mo (after small free tier) |
| Cloud Scheduler | ~$0.10 / job / mo (after 3 free) |
| Cloud KMS | ~$0.06 / key version / mo |


