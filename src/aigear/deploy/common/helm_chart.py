from pathlib import Path


def create_helm_chart(
        helm_location,
        service_name,
        service_image,
        service_ports,
        replicas: int = 1,
        port: int = 443,
        target_port: int = 50051
):
    helm_path = Path.cwd() / helm_location
    helm_path.mkdir(parents=True, exist_ok=True)

    deploy_template = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {service_name}-deployment
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {service_name}
  template:
    metadata:
      labels:
        app: {service_name}
    spec:
      containers:
      - name: {service_name}
        image: {service_image}
        ports:
        - containerPort: {service_ports}
---

apiVersion: v1
kind: Service
metadata:
  name: {service_name}
spec:
  type: LoadBalancer
  ports:
    - port: {port}
      targetPort: {target_port}
      protocol: TCP
  selector:
    app: {service_name}
"""
    with open(helm_path / "grpc_deployment.yaml", "w") as f:
        f.write(deploy_template)
