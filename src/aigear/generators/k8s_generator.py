"""
Kubernetes Configuration Generator

Generates Kubernetes YAML configurations for gRPC ML services.

Features:
- Deployment configuration
- Service (LoadBalancer/NodePort)
- ConfigMap for env.json
- HorizontalPodAutoscaler
- PersistentVolumeClaim for models
"""

from pathlib import Path
from typing import Dict, List, Optional
from jinja2 import Environment, FileSystemLoader, Template
import json
import yaml


class K8sConfigGenerator:
    """Kubernetes Configuration Generator"""

    def __init__(
        self,
        project_name: str,
        company: str,
        version: str,
        image_url: str,
        port: int = 50051,
        replicas: int = 2,
        resources: Optional[Dict] = None,
        autoscaling: Optional[Dict] = None,
        service_type: str = "LoadBalancer",
        output_dir: Optional[Path] = None,
    ):
        """
        Initialize K8s Config Generator

        Args:
            project_name: Project name
            company: Company code
            version: Version
            image_url: Docker image URL
            port: Service port
            replicas: Number of replicas
            resources: Resource requests/limits
            autoscaling: Autoscaling configuration
            service_type: Service type (LoadBalancer/NodePort/ClusterIP)
            output_dir: Output directory for YAML files
        """
        self.project_name = project_name
        self.company = company
        self.version = version
        self.image_url = image_url
        self.port = port
        self.replicas = replicas
        self.service_type = service_type
        self.output_dir = output_dir or Path.cwd() / "k8s"

        # Default resources
        self.resources = resources or {
            "requests": {
                "cpu": "500m",
                "memory": "1Gi"
            },
            "limits": {
                "cpu": "2000m",
                "memory": "4Gi"
            }
        }

        # Default autoscaling
        self.autoscaling = autoscaling or {
            "enabled": True,
            "minReplicas": 2,
            "maxReplicas": 10,
            "targetCPU": 70
        }

        # Service name
        self.service_name = f"{project_name}-{company}-{version}"

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, env_config: Dict) -> List[Path]:
        """
        Generate all Kubernetes configurations

        Args:
            env_config: Environment configuration (env.json content)

        Returns:
            List of generated file paths
        """
        generated_files = []

        # 1. Generate Deployment
        deployment_file = self.generate_deployment()
        generated_files.append(deployment_file)

        # 2. Generate Service
        service_file = self.generate_service()
        generated_files.append(service_file)

        # 3. Generate ConfigMap
        configmap_file = self.generate_configmap(env_config)
        generated_files.append(configmap_file)

        # 4. Generate HPA (if enabled)
        if self.autoscaling.get('enabled', False):
            hpa_file = self.generate_hpa()
            generated_files.append(hpa_file)

        # 5. Generate PVC for models
        pvc_file = self.generate_pvc()
        generated_files.append(pvc_file)

        return generated_files

    def generate_deployment(self) -> Path:
        """Generate Deployment YAML"""
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": self.service_name,
                "labels": {
                    "app": self.project_name,
                    "company": self.company,
                    "version": self.version
                }
            },
            "spec": {
                "replicas": self.replicas,
                "selector": {
                    "matchLabels": {
                        "app": self.project_name,
                        "company": self.company,
                        "version": self.version
                    }
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": self.project_name,
                            "company": self.company,
                            "version": self.version
                        }
                    },
                    "spec": {
                        "containers": [{
                            "name": "grpc-service",
                            "image": self.image_url,
                            "ports": [{
                                "containerPort": self.port,
                                "protocol": "TCP",
                                "name": "grpc"
                            }],
                            "env": [
                                {"name": "COMPANY", "value": self.company},
                                {"name": "VERSION", "value": self.version},
                                {"name": "PORT", "value": str(self.port)}
                            ],
                            "command": [
                                "/opt/venv/bin/python",
                                "/service/main.py",
                                "--company", self.company,
                                "--version", self.version
                            ],
                            "volumeMounts": [
                                {
                                    "name": "config",
                                    "mountPath": "/service/env.json",
                                    "subPath": "env.json"
                                },
                                {
                                    "name": "models",
                                    "mountPath": "/models"
                                }
                            ],
                            "resources": self.resources,
                            "livenessProbe": {
                                "exec": {
                                    "command": [
                                        "/bin/grpc_health_probe",
                                        "-addr=:{}".format(self.port)
                                    ]
                                },
                                "initialDelaySeconds": 10,
                                "periodSeconds": 10
                            },
                            "readinessProbe": {
                                "exec": {
                                    "command": [
                                        "/bin/grpc_health_probe",
                                        "-addr=:{}".format(self.port)
                                    ]
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5
                            }
                        }],
                        "volumes": [
                            {
                                "name": "config",
                                "configMap": {
                                    "name": f"{self.service_name}-config"
                                }
                            },
                            {
                                "name": "models",
                                "persistentVolumeClaim": {
                                    "claimName": f"{self.project_name}-models-pvc"
                                }
                            }
                        ]
                    }
                }
            }
        }

        output_file = self.output_dir / f"{self.service_name}-deployment.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(deployment, f, default_flow_style=False, sort_keys=False)

        return output_file

    def generate_service(self) -> Path:
        """Generate Service YAML"""
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": self.service_name,
                "labels": {
                    "app": self.project_name,
                    "company": self.company,
                    "version": self.version
                }
            },
            "spec": {
                "type": self.service_type,
                "selector": {
                    "app": self.project_name,
                    "company": self.company,
                    "version": self.version
                },
                "ports": [{
                    "port": self.port,
                    "targetPort": self.port,
                    "protocol": "TCP",
                    "name": "grpc"
                }]
            }
        }

        output_file = self.output_dir / f"{self.service_name}-service.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(service, f, default_flow_style=False, sort_keys=False)

        return output_file

    def generate_configmap(self, env_config: Dict) -> Path:
        """Generate ConfigMap YAML for env.json"""
        configmap = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{self.service_name}-config",
                "labels": {
                    "app": self.project_name,
                    "company": self.company,
                    "version": self.version
                }
            },
            "data": {
                "env.json": json.dumps(env_config, indent=2, ensure_ascii=False)
            }
        }

        output_file = self.output_dir / f"{self.service_name}-configmap.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(configmap, f, default_flow_style=False, sort_keys=False)

        return output_file

    def generate_hpa(self) -> Path:
        """Generate HorizontalPodAutoscaler YAML"""
        hpa = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": self.service_name,
                "labels": {
                    "app": self.project_name,
                    "company": self.company,
                    "version": self.version
                }
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": self.service_name
                },
                "minReplicas": self.autoscaling.get('minReplicas', 2),
                "maxReplicas": self.autoscaling.get('maxReplicas', 10),
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": self.autoscaling.get('targetCPU', 70)
                        }
                    }
                }]
            }
        }

        output_file = self.output_dir / f"{self.service_name}-hpa.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(hpa, f, default_flow_style=False, sort_keys=False)

        return output_file

    def generate_pvc(self) -> Path:
        """Generate PersistentVolumeClaim YAML for models"""
        pvc = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": f"{self.project_name}-models-pvc",
                "labels": {
                    "app": self.project_name
                }
            },
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "resources": {
                    "requests": {
                        "storage": "10Gi"
                    }
                },
                "storageClassName": "standard-rwo"
            }
        }

        output_file = self.output_dir / f"{self.project_name}-models-pvc.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(pvc, f, default_flow_style=False, sort_keys=False)

        return output_file

    def generate_cloudbuild_yaml(self, artifact_registry: str) -> Path:
        """
        Generate cloudbuild.yaml for CI/CD

        Args:
            artifact_registry: Artifact Registry URL

        Returns:
            Path to generated cloudbuild.yaml
        """
        cloudbuild = {
            "steps": [
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": [
                        "build",
                        "-t", f"{artifact_registry}/{self.project_name}:$COMMIT_SHA",
                        "-t", f"{artifact_registry}/{self.project_name}:latest",
                        "-f", "Dockerfile-grpc",
                        "."
                    ]
                },
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": [
                        "push",
                        f"{artifact_registry}/{self.project_name}:$COMMIT_SHA"
                    ]
                },
                {
                    "name": "gcr.io/cloud-builders/docker",
                    "args": [
                        "push",
                        f"{artifact_registry}/{self.project_name}:latest"
                    ]
                },
                {
                    "name": "gcr.io/cloud-builders/kubectl",
                    "args": [
                        "set",
                        "image",
                        f"deployment/{self.service_name}",
                        f"grpc-service={artifact_registry}/{self.project_name}:$COMMIT_SHA"
                    ],
                    "env": [
                        "CLOUDSDK_COMPUTE_REGION=$_REGION",
                        "CLOUDSDK_CONTAINER_CLUSTER=$_CLUSTER"
                    ]
                }
            ],
            "images": [
                f"{artifact_registry}/{self.project_name}:$COMMIT_SHA",
                f"{artifact_registry}/{self.project_name}:latest"
            ]
        }

        output_file = self.output_dir.parent / "cloudbuild.yaml"
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(cloudbuild, f, default_flow_style=False, sort_keys=False)

        return output_file


if __name__ == "__main__":
    # Example usage
    generator = K8sConfigGenerator(
        project_name="my-alc-service",
        company="trial",
        version="alc3",
        image_url="asia-northeast1-docker.pkg.dev/my-project/grpc-services/my-alc-service:latest",
        port=50051,
        replicas=2
    )

    # Load env.json
    env_config = {
        "projectName": "my-alc-service",
        "environment": "production",
        "grpc": {}
    }

    # Generate all configs
    files = generator.generate_all(env_config)
    print(f"Generated {len(files)} Kubernetes configuration files:")
    for f in files:
        print(f"  - {f}")
