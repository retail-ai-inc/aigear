<div align="center">

# One-command ML Deployment & Automation Framework

**Aigear is a Python library for deploying and managing machine learning pipelines in the cloud with minimal commands and configuration. It automates infrastructure setup, standardizes containerized pipeline execution, and ensures reproducibility through ephemeral compute—cutting deployment time from months to hours.**

[**What is Aigear**](##-What-is-Aigear?) · [**Quick Start**](##-Quick-Start) ·  [**View Demo**](docs/full-guide.md)·  [**Configuration Guide**](docs/route-guide.md)


</div>

## What is Aigear?

**For Data Scientists:**  
Aigear helps you deploy models to the cloud easily and safely. With one configuration file and a few commands, it builds everything needed to run your model—storage, scheduler, and compute—without writing DevOps scripts. It automates retraining, manages versions, and keeps deployments consistent.

**For MLOps / DevOps:**  
Aigear provides a unified CLI and API that automate infrastructure setup (storage, messaging, schedulers, compute, IAM), standardize containerized pipeline execution (data fetch, preprocessing, training, evaluation), and ensure reproducibility through ephemeral compute that self-terminates after runs. By consolidating configuration, logging, and secret management, Aigear minimizes DevOps overhead and enables scalable, repeatable ML workflows.

## Core Principles
- **Everything is a Task**: Model training, evaluation, packaging, and deployment are modeled as composable tasks.
- **Reproducible & Auditable**: Each run executes in a controlled container or ephemeral instance; configurations and outputs are traceable.
- **Least Privilege & Ephemeral Resources**: Use short-lived VMs or instances for jobs and automatically clean them up after completion to control cost and risk.

## Key Features
- **Infrastructure Automation**: Automatically create GCS buckets, Pub/Sub topics, Cloud Scheduler jobs, Service Accounts, and more.
- **Containerized Reproducible Runs**: Execute tasks inside predictable container environments to ensure reproducibility.
- **Scheduling & Auto-Retraining**: Support cron schedules, dependency steps, and automated retraining pipelines.
- **Versioning & Secrets**: Model versioning and configuration management with support for GCP Secret Manager.
- **Ephemeral Compute**: Launch short-lived VMs or Cloud Functions to run tasks and tear them down after completion.

## Why Aigear? Real-World Scenarios

### Story 1: No More "Waiting in Line for Deployment"
**Before Aigear:**  
You're a data scientist with a trained model ready to deploy. You hand it off to MLOps, who depend on DevOps to set up buckets, permissions, and schedulers. DevOps has ten projects in the queue. "Maybe next week," they say. Your model sits idle.

**After Aigear:**  
You open your terminal and run:
```bash
aigear gcp infra create
```
By the next morning, your entire infrastructure is ready—cloud storage, service accounts, scheduler, deployment environment. No tickets. No waiting. No dependencies.

**Result:** What used to take weeks now takes less than a day—and you did it yourself.

### Story 2: Multiple Teams, Consistent Infrastructure (Enterprise)
**Before Aigear:**  
At a large company, multiple teams run different ML pipelines—recommendation, pricing, forecasting. Each requests new GCP buckets, IAM roles, and scheduler jobs. Every setup goes through DevOps; every small mistake (wrong IAM role, mismatched bucket name) causes delays. A single project takes weeks of back-and-forth.

**After Aigear:**  
Each team uses a single configuration file (`env.json`) and runs:
```bash
aigear gcp infra create
aigear scheduler create --name rec-nightly --schedule "0 3 * * *" --step train
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
aigear task run --version v1 --step train
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

## Quick Start

1. **Install Aigear:**
```bash
pip install -U aigear
```

2. **Initialize a project:**
```bash
aigear-init --name xxx_ml_service
```

3. **Prepare `env.json`** (define GCP project, bucket, service accounts, etc.). See [configuration guide](docs/route-guide.md).

4. **Create infrastructure:**
```bash
aigear gcp infra create
```

5. **Create a scheduler job:**
```bash
aigear scheduler create --name rec-nightly --schedule "0 3 * * *" --step train
```

6. **Run a one-off task:**
```bash
aigear task run --version v1 --step train
```

## Supported Platforms
- **Current:** Google Cloud Platform (GCS, Pub/Sub, Cloud Scheduler, Cloud Functions/VM) and Slack notifications.
- **Planned:** AWS, Azure, Alibaba Cloud, internal company infrastructure, and additional notification channels (Teams, Discord, Email).

## Limitations & Roadmap

**Current Support:**
- Storage: Google Cloud Storage (bucket create/upload/download/copy)
- Messaging & scheduling: Pub/Sub + Cloud Scheduler integration
- Compute: VM creation via Cloud Function + startup script (ephemeral, self-terminating)
- Databases: MongoDB connectivity via URI or Google Secret Manager
- Configuration: JSON-driven (`env.json`) with schema validation (Pydantic)

**Limitations:**
- Requires broad Google Cloud IAM permissions (Storage Admin, Pub/Sub Admin, Scheduler Admin, Cloud Functions Developer, Service Account Admin)
- Pipeline orchestration is step-based only—no DAG/topology analysis or dependency management yet
- Only supports MongoDB as a database connector; no built-in support for SQL, BigQuery, etc.
- Tightly coupled to Google Cloud—no AWS/Azure support yet

**Roadmap (Planned):**
- Unified CLI with enhanced subcommands for infra, pipelines, and tasks
- Improved error handling and retries (e.g., failed task requeue)
- Broader database support (Postgres, BigQuery)
- More flexible execution environments (Cloud Run jobs, GKE, hybrid)
- Richer orchestration: DAG/task dependency parsing for controlled parallel execution

## Aiger Architecture

## Practical Constraints

- **GCP-only (today):** Aigear is designed for Google Cloud; AWS/Azure support is planned.
- **IAM requirements:** Infrastructure bootstrap requires elevated IAM roles. Plan a secure process for granting these to a CI account or privileged operator.
- **Not yet a full DAG orchestrator:** Aigear schedules and executes steps but does not yet do dependency analysis or automatic topology optimization. For complex DAGs, treat Aigear as the runner + infra automation layer.
- **Testing & staging:** Run infrastructure creation and pipeline runs in a dedicated staging GCP project before enabling production.

## One-line Pitches
- **ML Engineer:** "Run and schedule training with one command; results and configs are reproducible."
- **DevOps / SRE:** "Standardized infra creation that reduces manual errors and is auditable."
- **Product Manager:** "Faster model iterations with controlled cost and secure secrets."

## Contributing & Contact
Contributions, issues, and PRs are welcome. Share internal use-cases to help evolve common conventions. For questions or feature requests, open an issue in the repository or contact the maintainers.

---