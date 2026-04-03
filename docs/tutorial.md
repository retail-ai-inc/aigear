# Tutorial: Sklearn Pipeline

This tutorial walks through a complete, end-to-end example — training a **Logistic Regression** classifier on the breast cancer dataset and deploying it as a **gRPC microservice** — using the `aigear_sklearn_pipeline` demo located in `example/aigear_sklearn_pipeline/`.

For a quick reference of all configuration parameters, see the [Configuration Guide](route-guide.md). For full CLI documentation, see the [CLI Reference](cli-reference.md).

---

## Prerequisites

Before starting, make sure the following tools are installed and authenticated:

- **Python 3.9+** with `pip install -U aigear`
- **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** — with **Kubernetes enabled** (Settings → Kubernetes → Enable Kubernetes)
- **[gcloud CLI](https://cloud.google.com/sdk/docs/install)** — authenticated via `gcloud auth login`
- **[kubectl](https://kubernetes.io/docs/tasks/tools/)** — for Kubernetes deployments

> **No GCP account?** You can still follow Steps 1–7 locally by setting `gcs_switch = False` in `src/pipelines/common/constant.py`. GCS, Cloud Scheduler, and GKE steps require a GCP project.

---

## What We're Building

```
Breast Cancer Dataset (sklearn)
        │
        ▼
  [fetch_data]  ──── saves breast_cancer.pkl ──────────────────► GCS bucket
        │
        ▼
[preprocessing] ──── StandardScaler + train/test split ─────────► GCS bucket
        │              saves features_data.pkl + scaler_model.pkl
        ▼
  [training]    ──── LogisticRegression.fit() ─────────────────► GCS bucket
        │              saves logistic_regression.pkl
        ▼
[model_service] ──── loads scaler + model, serves gRPC predict()
```

Each step runs as an isolated Python function, containerized in Docker, and optionally scheduled on GCP Cloud Scheduler. The trained model is then deployed as a gRPC microservice locally or on GKE.

---

## 1. Project Structure

The demo project lives in `example/aigear_sklearn_pipeline/`. Its directory layout mirrors what `aigear-init` generates:

```text
aigear_sklearn_pipeline/
├── config_schema/               # Auto-generated Pydantic schema (from aigear-env-schema)
├── src/
│   └── pipelines/
│       ├── common/
│       │   └── constant.py      # Shared constants (e.g. gcs_switch)
│       └── logistic_regression/ # Pipeline version directory
│           ├── fetch_data/
│           │   └── data_from_sklearn.py
│           ├── preprocessing/
│           │   └── feature_processing.py
│           ├── training/
│           │   └── train.py
│           └── model_service/
│               ├── logistic_regression_service.py
│               └── grpc_deployment.yaml
├── Dockerfile.pl                # Pipeline container (fetch_data → preprocessing → training)
├── Dockerfile.ms                # Model service container (gRPC inference server)
├── requirements_pl.txt          # Pipeline dependencies: aigear, numpy, scikit-learn
├── requirements_ms.txt          # Model service dependencies
└── env.sample.json              # Configuration template — copy to env.json
```

> **Note on pipeline version naming:** In this demo the pipeline version directory is named `logistic_regression` (not `v1`). Aigear uses this directory name as the `pipeline_version` argument passed to every step function.

---

## 2. Configure `env.json`

Copy the sample configuration and fill in your GCP details:

```bash
cd example/aigear_sklearn_pipeline
cp env.sample.json env.json
```

The key sections in `env.json` for this demo:

```json
{
    "project_name": "aigear_sklearn_pipeline",
    "environment": "local",
    "aigear": {
        "gcp": {
            "gcp_project_id": "YOUR_GCP_PROJECT_ID",
            "location": "asia-northeast1",
            "bucket": {
                "on": true,
                "bucket_name": "test-sklearn-pipeline",
                "bucket_name_for_release": "test-sklearn-pipeline-service"
            },
            "kms": {
                "on": true,
                "keyring_name": "test-sklearn-pipeline-keyring",
                "key_name": "test-sklearn-pipeline-key"
            },
            "cloud_function": {
                "on": true,
                "function_name": "test-sklearn-pipeline-run"
            },
            "iam": {
                "on": true,
                "account_name": "test-sklearn-pipeline"
            },
            "pub_sub": {
                "on": true,
                "topic_name": "test-sklearn-pipeline-pubsub"
            },
            "artifacts": {
                "on": true,
                "repository_name": "test-sklearn-pipeline-images",
                "ms_image_name": "test-sklearn-model-service",
                "pl_image_name": "test-sklearn",
                "image_tag": "latest"
            },
            "kubernetes": {
                "on": true,
                "cluster_name": "test-sklearn-pipeline-service-cluster",
                "num_nodes": 1,
                "min_nodes": 1,
                "max_nodes": 5
            }
        },
        "slack": { "on": false, "webhook_url": "" }
    },
    "pipelines": {
        "logistic_regression": {
            "scheduler": {
                "name": "test-sklearn-pipeline",
                "schedule": "45 21 * * 0",
                "time_zone": "Asia/Tokyo"
            },
            "fetch_data": {
                "parameters": { "data_file_name": "breast_cancer.pkl" },
                "resources": { "vm_name": "test-sklearn-fetch-data-vm", "disk_size_gb": "50", "spec": "e2-medium", "gpu": false },
                "pipeline_step": "src.pipelines.logistic_regression.fetch_data.data_from_sklearn.fetch_data"
            },
            "preprocessing": {
                "parameters": {
                    "feature_file_name": "features_data.pkl",
                    "scaler_model": "scaler_model.pkl"
                },
                "resources": { "vm_name": "test-sklearn-preprocessing-vm", "disk_size_gb": "50", "spec": "e2-medium", "gpu": false },
                "pipeline_step": "src.pipelines.logistic_regression.preprocessing.feature_processing.feature_processing"
            },
            "training": {
                "parameters": { "logistic_model": "logistic_regression.pkl" },
                "resources": { "vm_name": "test-sklearn-training-vm", "disk_size_gb": "50", "spec": "e2-medium", "gpu": false },
                "pipeline_step": "src.pipelines.logistic_regression.training.train.train_model"
            },
            "model_service": {
                "release": true,
                "grpc": {
                    "service_host": "0.0.0.0",
                    "port": "50051",
                    "multi_processing": { "on": false, "process_count": 3, "thread_count": 1, "disable_omp": true },
                    "sentry": { "on": false, "dsn": "", "traces_sample_rate": 1.0 }
                },
                "resources": { "vm_name": "test-sklearn-model-service-vm", "disk_size_gb": "50", "spec": "e2-medium", "gpu": false },
                "model_class_path": "src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService"
            }
        }
    }
}
```

---

## 3. Generate Config Schema (Recommended)

Auto-generate a typed Pydantic schema from your `env.json` so all pipeline code gets full IDE auto-complete:

```bash
aigear-env-schema --generate
# Force regenerate after env.json changes
aigear-env-schema --generate --force
```

This writes a schema to `config_schema/env_schema.py`. Every pipeline step imports it:

```python
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema

env_config = EnvConfig.get_config_with_schema(EnvSchema)

# Fully typed — no magic string keys
project_id   = env_config.aigear.gcp.gcp_project_id
bucket_name  = env_config.aigear.gcp.bucket.bucket_name
data_file    = env_config.pipelines.logistic_regression.fetch_data.parameters.data_file_name
```

---

## 4. Create GCP Infrastructure

This single command provisions all GCP resources declared in `env.json`:

```bash
aigear-gcp-infra --create
```

Resources are created in three phases:

1. **Service Account** — created first; IAM bindings wait for propagation automatically
2. **Buckets, Artifact Registry, Pub/Sub, KMS, Kubernetes** — run in **parallel**
3. **Cloud Function** — created last (depends on the Pub/Sub topic from Phase 2)

Each step is idempotent — re-running the command safely skips already-existing resources. The log output uses structured JSON and shows only meaningful status per step.

> **Requires owner-level GCP access.** Infrastructure creation takes approximately 2 hours for a full GKE cluster. Run this from Cloud Shell or a machine with `gcloud auth login` completed.

---

## 5. Pipeline Step Implementation

Each step is a plain Python function that accepts `pipeline_version` as its only argument. `AssetManagement` handles local ↔ GCS file I/O transparently based on the `bucket_on` flag.

### 5.1 `fetch_data` — Load the dataset

`src/pipelines/logistic_regression/fetch_data/data_from_sklearn.py`

```python
from sklearn.datasets import load_breast_cancer
import pickle
from aigear.common.logger import Logging
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch

logger = Logging(log_name=__name__).console_logging()


def get_data(save_path):
    data = load_breast_cancer()
    with open(save_path, "wb") as f:
        pickle.dump(data, f)


def fetch_data(pipeline_version):
    logger.info("-----fetch data-----")
    env_config = EnvConfig.get_config_with_schema(EnvSchema)
    asset_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="dataset",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    data_file_name = env_config.pipelines.logistic_regression.fetch_data.parameters.data_file_name
    save_path = asset_management.get_local_path(local_file_name=data_file_name)
    get_data(save_path)
    asset_management.upload(data_file_name)
    logger.info("-----fetch data completed-----")
```

**What it does:** Loads the sklearn breast cancer dataset, pickles it locally, and uploads `breast_cancer.pkl` to GCS under `dataset/logistic_regression/`.

---

### 5.2 `preprocessing` — Feature engineering

`src/pipelines/logistic_regression/preprocessing/feature_processing.py`

```python
import pickle
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch


def feature_processing(pipeline_version):
    env_config = EnvConfig.get_config_with_schema(EnvSchema)

    # Download raw dataset from GCS
    dataset_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="dataset",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    data_file_name = env_config.pipelines.logistic_regression.fetch_data.parameters.data_file_name
    data_local_path = dataset_management.download(file_name=data_file_name)

    with open(data_local_path, "rb") as f:
        dataset = pickle.load(f)

    # Split and scale
    x_train, x_test, y_train, y_test = train_test_split(
        dataset.data, dataset.target, test_size=0.2, random_state=42, stratify=dataset.target
    )
    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_test_scaled  = scaler.transform(x_test)

    # Upload processed features and fitted scaler
    feature_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="feature",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    feature_file_name = env_config.pipelines.logistic_regression.preprocessing.parameters.feature_file_name
    feature_path = feature_management.get_local_path(local_file_name=feature_file_name)
    with open(feature_path, "wb") as f:
        pickle.dump([x_train_scaled, x_test_scaled, y_train, y_test], f)
    feature_management.upload(feature_file_name)

    scaler_file_name = env_config.pipelines.logistic_regression.preprocessing.parameters.scaler_model
    scaler_path = feature_management.get_local_path(local_file_name=scaler_file_name)
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    feature_management.upload(scaler_file_name)
```

**What it does:** Downloads `breast_cancer.pkl`, applies an 80/20 stratified split, fits a `StandardScaler`, and uploads `features_data.pkl` and `scaler_model.pkl` to GCS under `feature/logistic_regression/`.

---

### 5.3 `training` — Model training

`src/pipelines/logistic_regression/training/train.py`

```python
import pickle
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch


def train_model(pipeline_version):
    env_config = EnvConfig.get_config_with_schema(EnvSchema)

    # Download pre-processed features
    feature_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="feature",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    feature_file_name = env_config.pipelines.logistic_regression.preprocessing.parameters.feature_file_name
    features_path = feature_management.download(feature_file_name)
    with open(features_path, "rb") as f:
        x_train, x_test, y_train, y_test = pickle.load(f)

    # Train
    model = LogisticRegression(max_iter=1000, random_state=42)
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test)
    print("Accuracy:", accuracy_score(y_test, y_pred))
    print(classification_report(y_test, y_pred))

    # Upload trained model
    training_management = AssetManagement(
        pipeline_version=pipeline_version,
        data_type="training",
        project_id=env_config.aigear.gcp.gcp_project_id,
        bucket_name=env_config.aigear.gcp.bucket.bucket_name,
        bucket_on=gcs_switch,
    )
    model_name = env_config.pipelines.logistic_regression.training.parameters.logistic_model
    model_path = training_management.get_local_path(model_name)
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    training_management.upload(model_name)
```

**What it does:** Downloads `features_data.pkl`, fits a `LogisticRegression`, prints accuracy and classification report, and uploads `logistic_regression.pkl` to GCS under `training/logistic_regression/`.

---

### 5.4 `model_service` — gRPC inference

`src/pipelines/logistic_regression/model_service/logistic_regression_service.py`

```python
import pickle
import numpy as np
from aigear.management.asset import AssetManagement
from aigear.common.config import EnvConfig
from config_schema.env_schema import EnvSchema
from src.pipelines.common.constant import gcs_switch


class ModelService:
    def __init__(self):
        self.scaler_model, self.logistic_model = self.load_all_model()

    def predict(self, data):
        features = np.array([data["features"]])
        features_scaled = self.scaler_model.transform(features)
        predict_class = self.logistic_model.predict(features_scaled)
        print("Model prediction results:", predict_class[0])
        return predict_class.tolist()

    @staticmethod
    def _load_model(model_path):
        with open(model_path, "rb") as f:
            return pickle.load(f)

    def load_all_model(self):
        env_config = EnvConfig.get_config_with_schema(EnvSchema)

        feature_management = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="feature",
            project_id=env_config.aigear.gcp.gcp_project_id,
            bucket_name=env_config.aigear.gcp.bucket.bucket_name,
            bucket_on=gcs_switch,
        )
        scaler_model_name = env_config.pipelines.logistic_regression.preprocessing.parameters.scaler_model
        scaler_model_path = feature_management.download(scaler_model_name)

        training_management = AssetManagement(
            pipeline_version="logistic_regression",
            data_type="training",
            project_id=env_config.aigear.gcp.gcp_project_id,
            bucket_name=env_config.aigear.gcp.bucket.bucket_name,
            bucket_on=gcs_switch,
        )
        model_name = env_config.pipelines.logistic_regression.training.parameters.logistic_model
        model_path = training_management.download(model_name)

        return self._load_model(scaler_model_path), self._load_model(model_path)
```

**What it does:** On startup, downloads the fitted scaler and trained model from GCS. The `predict(data)` method applies the scaler then returns the logistic regression prediction. Aigear wraps this class in a gRPC server automatically.

---

## 6. Docker Images(Artifact Registry)

The demo uses two separate Dockerfiles with [uv](https://github.com/astral-sh/uv) for fast dependency installation:

**`Dockerfile.pl`** (pipeline — fetch_data, preprocessing, training):
```dockerfile
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /pl
COPY . .

RUN uv python install 3.12.7
RUN uv venv /opt/venv/pl --python 3.12.7
RUN . /opt/venv/pl/bin/activate && uv pip install -r requirements_pl.txt

ENV VIRTUAL_ENV=/opt/venv/pl
ENV PATH="/opt/venv/pl/bin:$PATH"
```

**`requirements_pl.txt`**:
```
aigear==0.0.1
numpy==2.4.2
scikit_learn==1.8.0
```

**`Dockerfile.ms`** (model service — gRPC server) follows the same pattern using `requirements_ms.txt`.

Build both images:

```bash
# Build locally (no push)
aigear-image --create
```

> **Local build:** Make sure `gcs_switch = False` in `src/pipelines/common/constant.py`. When running locally, files are stored under `asset/` instead of GCS.

---

## 7. Run Pipeline Steps Locally

Run docker compose to test the pipeline and model service in a containerized environment.

By default, `docker-compose-pl.yml` builds a fresh image from `Dockerfile.pl` on every run. If you already built the image in Step 6, you can reuse it by commenting out the `build:` block and uncommenting the `image:` line in `docker-compose-pl.yml`:

```yaml
services:
  pipeline_training:
    # build:
    #   context: .
    #   dockerfile: Dockerfile.pl
    image: pl_test:latest   # reuse the image built in Step 6
```

This skips the rebuild and speeds up the startup. Either approach works.

```bash
# Run the training pipeline (fetch_data → preprocessing → training)
docker compose -f docker-compose-pl.yml up -d
# Follow the logs to watch progress
docker compose -f docker-compose-pl.yml logs -f

# Run the gRPC model service
docker compose -f docker-compose-ms.yml up -d
```

The gRPC server is now reachable at `localhost:50051`. Send a prediction request using any gRPC client with a payload like:

```json
{ "features": [17.99, 10.38, 122.8, 1001.0, 0.1184, ...] }
```

Tear down:

```bash
docker compose -f docker-compose-pl.yml down
docker compose -f docker-compose-ms.yml down
```

---

## 8. Deploy the gRPC Model Service to kubernetes
### Local Kubernetes (Docker Desktop)
Before deploying to GKE, validate the Kubernetes deployment locally using **Docker Desktop's built-in Kubernetes**. Enable it via **Settings → Kubernetes → Enable Kubernetes**.

> **Tip:** Docker Desktop uses **kubeadm** to provision its local cluster and shares the same Docker daemon as the host. This means any image built with `docker build` (or `aigear-image --create`) is immediately available to the cluster — no registry push required. `imagePullPolicy: Never` is set automatically for local deployments to enforce this behaviour.

**Prerequisites**

- Build the local Docker image (Step 6): `aigear-image --create`
- Run the training pipeline with `gcs_switch = False` (Step 7) so model files are written to `asset/`
- Verify the `image:` field in `grpc_deployment_local.yaml` matches the image name built in Step 6. Since `imagePullPolicy: Never`, Kubernetes will only look for the image locally — the name must match exactly:

  ```yaml
  image: asia-northeast1-docker.pkg.dev/<your-project>/test-sklearn-pipeline-images/aigear-sklearn-pipeline-service:latest
  ```

**Deploy**

```bash
aigear-deploy-model --version logistic_regression --model_class_path src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService
```

This command generates `grpc_deployment_local.yaml` (if it does not yet exist), switches kubectl context to `docker-desktop`, and applies the manifest. The local `asset/` directory is automatically mounted into the pod at `/ms/asset`, so no GCS connection is required.

**Verify**

Check the pod logs (replace the pod suffix with the actual value from `kubectl get pods`):

```bash
kubectl logs aigear-sklearn-pipeline-logistic-regression-service-<pod-suffix>
```

Check the external IP (Docker Desktop assigns `localhost`):

```bash
kubectl get service aigear-sklearn-pipeline-logistic-regression-service
```

The gRPC server is reachable at `localhost:50051` once `EXTERNAL-IP` shows `localhost`.

**Delete**

```bash
aigear-deploy-model --version logistic_regression --model_class_path src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService --delete
```

---

## 9. Push Images to Artifact Registry

Once the pipeline has been validated locally, push the images to GCP Artifact Registry:

```bash
aigear-image --create --push
```

> **Before pushing to GCP:** Make sure `gcs_switch = True` in `src/pipelines/common/constant.py`. Pipeline steps running on GCP need to read and write files via GCS.

---

## 10. Schedule on GCP

Once the pipeline has been validated end-to-end, create a Cloud Scheduler job to run all steps automatically every Sunday at 21:45 JST (as configured in `env.json`):

```bash
aigear-scheduler --create  --version logistic_regression  --step_names fetch_data,preprocessing,training,model_service
```

After creation, go to [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler) in the GCP Console to manually trigger an immediate run and confirm everything works in production.

---

## 11. End-to-End Command Reference

```bash
cd example/aigear_sklearn_pipeline

# ── Step 2: Configure env.json ────────────────────────────────────────────────
cp env.sample.json env.json
# Edit env.json: set gcp_project_id, bucket_name, etc.

# ── Step 3: Generate typed config schema ──────────────────────────────────────
aigear-env-schema --generate

# ── Step 4: Provision GCP infrastructure (owner-level access required) ────────
aigear-gcp-infra --create

# ── Step 5: Implement pipeline code ───────────────────────────────────────────
# Fill in src/pipelines/logistic_regression/{fetch_data,preprocessing,training,model_service}/

# ── Step 6: Build Docker images locally ───────────────────────────────────────
# Requires: gcs_switch = False in src/pipelines/common/constant.py
aigear-image --create

# ── Step 7: Run pipeline and model service locally (Docker Compose) ───────────
# Option A (default): build fresh image from Dockerfile on every run
docker compose -f docker-compose-pl.yml up -d
docker compose -f docker-compose-ms.yml up -d

# Option B: reuse the image built in Step 6
# → In docker-compose-pl.yml, comment out build: and uncomment image:
# docker compose -f docker-compose-pl.yml up -d

# Tear down
docker compose -f docker-compose-pl.yml down
docker compose -f docker-compose-ms.yml down

# ── Step 8: Deploy gRPC model service to local Kubernetes (Docker Desktop) ────
# Requires: image from Step 6, gcs_switch = False (model files in asset/)
aigear-deploy-model \
    --version logistic_regression \
    --model_class_path src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService

# Verify
kubectl get service aigear-sklearn-pipeline-logistic-regression-service
kubectl logs aigear-sklearn-pipeline-logistic-regression-service-<pod-suffix>

# Delete
aigear-deploy-model \
    --version logistic_regression \
    --model_class_path src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService \
    --delete

# ── Step 9: Push images to Artifact Registry ──────────────────────────────────
# Requires: gcs_switch = True in src/pipelines/common/constant.py
aigear-image --create --push

# ── Step 10: Schedule recurring pipeline runs on GCP ──────────────────────────
aigear-scheduler --create \
    --version logistic_regression \
    --step_names fetch_data,preprocessing,training
```
