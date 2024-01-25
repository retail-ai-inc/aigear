# This script is used to test deployment
# pip install aigear-0.0.1-py3-none-any.whl
from aigear.client import MlgrpcClient

grpc_client = MlgrpcClient(
    service_host='localhost:50051'
)

data = {"features": [6.4, 2.8, 5.6, 2.1]}

output = grpc_client.predict(data)

print(f'Model output: {output}')
