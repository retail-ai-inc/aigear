<div align="center">

# Cloud-Native ML Deployment & Automation Framework

[**Installation**](#installation) · [**Quick Start**](#quick-start) · [**CLI Reference**](docs/cli-reference.md) · [**View Demo**](docs/full-guide.md) · [**Configuration Guide**](docs/route-guide.md)

</div>

## What is Aigear?

Aigear is a Python package that closes the gap between model development and production deployment. Instead of coordinating across data science, MLOps, and DevOps teams to set up cloud infrastructure, containerize pipelines, and expose model APIs, you describe your project once in `env.json` and let Aigear handle the rest—provisioning GCP resources, building Docker images, scheduling pipeline runs on ephemeral compute, and deploying models as gRPC microservices.

---

## Installation

### Prerequisites

Aigear provisions GCP resources and manages Kubernetes deployments. The following CLI tools must be installed and authenticated before use:

- **[gcloud CLI](https://cloud.google.com/sdk/docs/install)** — required for all GCP operations (infrastructure, Cloud Scheduler, Artifact Registry, etc.)
  ```bash
  gcloud auth login
  gcloud config set project YOUR_PROJECT_ID
  ```
- **[kubectl](https://kubernetes.io/docs/tasks/tools/)** — required only if deploying the gRPC model service to GCP Kubernetes (`aigear-deploy-model`)

### Install Aigear

```bash
pip install -U aigear
```

---

## Quick Start

### 1. Initialize a project

```bash
aigear-init --name my_ml_service --pipeline_versions v1,v2
```

This creates the following project structure:

```
my_ml_service/
├── cloudbuild/
│   └── cloudbuild.yaml
├── docs/
├── kms/
├── src/
│   └── pipelines/
│       ├── v1/
│       │   ├── fetch_data/
│       │   ├── preprocessing/
│       │   ├── training/
│       │   └── model_service/
│       └── v2/
│           ├── fetch_data/
│           ├── preprocessing/
│           ├── training/
│           └── model_service/
├── Dockerfile.pl               # Pipeline container
├── Dockerfile.pl.dockerignore
├── docker-compose-pl.yml
├── requirements_pl.txt
├── Dockerfile.ms               # Model service container
├── Dockerfile.ms.dockerignore
├── docker-compose-ms.yml
├── requirements_ms.txt
├── env.sample.json
└── README.md
```

> **Two Dockerfiles:** `Dockerfile.pl` is for the training pipeline; `Dockerfile.ms` is for the gRPC model serving service.

### 2. Configure `env.json`

Copy `env.sample.json` to `env.json` and fill in your GCP project, bucket, service accounts, etc. See the [configuration guide](docs/route-guide.md).

### 3. Create GCP infrastructure

```bash
aigear-gcp-infra
```

### 4. Generate env schema (optional)

```bash
aigear-env-schema
```

Auto-generates a Pydantic model from your `env.json`. This gives you full type hints and IDE auto-complete when reading configuration, so you can navigate from any variable directly back to its definition in `env.json` instead of looking up string keys manually.

### 5. Implement your pipeline

Fill in the generated scaffold with your own code:

- **Pipeline steps** — implement each stage under `src/pipelines/v1/` (e.g., `fetch_data/`, `preprocessing/`, `training/`, `model_service/`).
- **Dockerfiles** — edit `Dockerfile.pl` (training pipeline) and `Dockerfile.ms` (model service) to install your dependencies. The generated files include working templates you can build on.
- **Dependencies** — add your Python packages to `requirements_pl.txt` and/or `requirements_ms.txt`.

### 6. Build Docker images

```bash
# Build both pipeline and model service images (default)
aigear-image

# Build and push a specific image
aigear-image --dockerfile_path Dockerfile.pl --image_name my-pipeline --image_version v1 --push
```

### 7. Schedule pipeline steps

Creates a Cloud Scheduler job on GCP that triggers the specified pipeline steps on a cron schedule defined in `env.json`.

```bash
aigear-scheduler --version v1 --step_names fetch_data,preprocessing,training
```

> **Tip:** Once created, you can go to [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler) in the GCP Console to manually trigger an immediate run. A `--run` flag for triggering directly from the CLI is planned but not yet available (`aigear-scheduler --version v1 --run`).

---

## Key Features

- **Infrastructure Automation**: Automatically create GCS buckets, Pub/Sub topics, Cloud Scheduler jobs, Service Accounts, and more.
- **Containerized Reproducible Runs**: Execute tasks inside predictable container environments to ensure reproducibility.
- **Scheduling & Auto-Retraining**: Support cron schedules, dependency steps, and automated retraining pipelines.
- **Versioning & Secrets**: Model versioning and configuration management with support for GCP Secret Manager.
- **Ephemeral Compute**: Launch short-lived VMs or Cloud Functions to run tasks and tear them down after completion.
- **gRPC Model Serving**: Deploy ML models as gRPC microservices locally or on GCP.

---

## Architecture

![Aigear Architecture](docs/images/architecture.png)

---

## Core Principles

- **Everything is a Task**: Model training, evaluation, packaging, and deployment are modeled as composable tasks.
- **Reproducible & Auditable**: Each run executes in a controlled container or ephemeral instance; configurations and outputs are traceable.
- **Least Privilege & Ephemeral Resources**: Use short-lived VMs or instances for jobs and automatically clean them up after completion to control cost and risk.
- **Centralized, Not Scattered**: Training pipeline and model service code live together under a single versioned project. Configuration, secrets, and infrastructure definitions are consolidated in one `env.json`—no more hunting across repos, scripts, and dashboards.
- **You Own the Code**: Aigear scaffolds the structure and handles infrastructure, but your pipeline logic runs as plain Python. No proprietary SDK to wrap your code in, no lock-in to a platform's execution model—making it easy to understand, extend, and debug compared to more opinionated MLOps platforms.

---

## Why Aigear? Real-World Scenarios

### Story 1: No More "Waiting in Line for Deployment"
**Before Aigear:**
You're a data scientist with a trained model ready to deploy. You hand it off to MLOps, who depend on DevOps to set up buckets, permissions, and schedulers. DevOps has ten projects in the queue. "Maybe next week," they say. Your model sits idle.

**After Aigear:**
You open your terminal and run:
```bash
aigear-gcp-infra
```
By the next morning, your entire infrastructure is ready—cloud storage, service accounts, scheduler, deployment environment. No tickets. No waiting. No dependencies.

**Result:** What used to take weeks now takes less than a day—and you did it yourself.

### Story 2: Multiple Teams, Consistent Infrastructure (Enterprise)
**Before Aigear:**
At a large company, multiple teams run different ML pipelines—recommendation, pricing, forecasting. Each requests new GCP buckets, IAM roles, and scheduler jobs. Every setup goes through DevOps; every small mistake (wrong IAM role, mismatched bucket name) causes delays. A single project takes weeks of back-and-forth.

**After Aigear:**
Each team uses a single configuration file (`env.json`) and runs:
```bash
aigear-gcp-infra
aigear-scheduler --version v1 --step_names fetch_data,training,evaluate
```
Aigear automatically creates what's missing, verifies what exists, and keeps naming and permissions consistent across teams.

**Result:** One shared pattern, no manual approval steps, fully auditable infrastructure creation.
**Who wins:** DevOps (less manual provisioning), ML Engineers (instant setup), Product (faster iteration).

### Story 3: "It Worked on My Laptop" → Perfect Reproducibility
**Before Aigear:**
You're prototyping new models daily. Your teammate's code fails on your machine because dependencies differ. Someone uses Python 3.8, someone else 3.11. Secrets live in random `.env` files. It's impossible to reproduce a successful experiment from two months ago.

**After Aigear:**
Every pipeline runs inside a Docker container with the exact same environment each time. Secrets are stored securely, configurations are validated, and results are logged automatically.
```bash
aigear-workflow --version v1 --step src.pipelines.v1.training.run
```
Aigear spins up a temporary VM, runs the container, stores the output in cloud storage, and shuts everything down.

**Result:** Perfect reproducibility—the same run, same results, every time.
**Who wins:** Data scientists (less debugging), QA teams (easy re-runs), management (consistent experiments).

### Story 4: Cost Control with Ephemeral Infrastructure
**Before Aigear:**
A team launches several training VMs and forgets to turn them off. The next month, the cloud bill explodes—unused machines kept running all week.

**After Aigear:**
Every VM created by Aigear is ephemeral—it spins up when a job starts, runs your container, and deletes itself right after. Schedulers ensure no resources stay idle.

**Result:** You only pay for what actually runs. No idle servers. No surprise bills.
**Who wins:** Finance teams (predictable billing), engineering (no cleanup scripts).

---

## CLI Reference

See the full [CLI Reference](docs/cli-reference.md) for all commands and arguments.

| Command | Description |
|---|---|
| `aigear-init` | Initialize a new project scaffold |
| `aigear-gcp-infra` | Create GCP infrastructure (buckets, IAM, Pub/Sub, schedulers) |
| `aigear-workflow` | Run a single pipeline step locally |
| `aigear-scheduler` | Create a Cloud Scheduler job for pipeline steps |
| `aigear-image` | Build and optionally push Docker images to Artifact Registry |
| `aigear-grpc` | Start a gRPC model serving server |
| `aigear-deploy-model` | Deploy or delete a gRPC model service (local or GCP) |
| `aigear-env-schema` | Auto-generate a Pydantic schema from `env.json` |

---

## Supported Platforms & Roadmap

**Currently supported:**
- **Cloud:** Google Cloud Platform (GCS, Pub/Sub, Cloud Scheduler, Cloud Functions, Compute Engine, Kubernetes Engine, Artifact Registry)
- **Notifications:** Slack
- **Databases:** MongoDB (via URI or GCP Secret Manager)
- **Compute:** Ephemeral VMs and Cloud Run (self-terminating after each job)

**Known limitations:**
- Requires broad GCP IAM permissions (Storage Admin, Pub/Sub Admin, Scheduler Admin, Cloud Functions Developer, Service Account Admin)
- Pipeline orchestration is step-based only—no DAG/dependency analysis yet
- No built-in SQL or BigQuery connector
- IAM bootstrap requires elevated roles; run infrastructure creation in a dedicated staging project before production

**Planned:**
- AWS and Azure support
- Broader database support (Postgres, BigQuery)
- More execution environments (Cloud Run jobs, GKE, hybrid)
- DAG/task dependency parsing for controlled parallel execution
- Improved error handling and task retry

---

## Contributing & Contact

Contributions, issues, and PRs are welcome. Share internal use-cases to help evolve common conventions. For questions or feature requests, open an issue in the repository or contact the maintainers.
