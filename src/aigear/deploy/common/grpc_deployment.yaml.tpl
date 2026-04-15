apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${service_name}
spec:
  replicas: ${replicas}
  selector:
    matchLabels:
      app: ${service_name}
  template:
    metadata:
      labels:
        app: ${service_name}
    spec:
      containers:
      - name: ${service_name}
        image: ${service_image}
        imagePullPolicy: ${image_pull_policy}
        ${grpc_command}
        ports:
        - containerPort: ${service_ports}${asset_volume_mount}${asset_volume}
---

apiVersion: v1
kind: Service
metadata:
  name: ${service_name}
spec:
  type: LoadBalancer
  ports:
    - port: ${port}
      targetPort: ${service_ports}
      protocol: TCP
  selector:
    app: ${service_name}
