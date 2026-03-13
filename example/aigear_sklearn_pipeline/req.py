import grpc
from aigear.service.grpc.protos import grpc_pb2
from aigear.service.grpc.protos import grpc_pb2_grpc
from google.protobuf.struct_pb2 import Struct


def predict():
    features = [
        14.25, 19.50, 92.30, 630.0, 0.098, 0.110, 0.095, 0.055, 0.180, 0.062,
        0.320, 1.250, 2.500, 30.0, 0.0065, 0.025, 0.030, 0.012, 0.020, 0.0038,
        16.20, 25.00, 105.0, 760.0, 0.130, 0.260, 0.280, 0.120, 0.290, 0.085
    ]

    channel = grpc.insecure_channel("localhost:50051")
    stub = grpc_pb2_grpc.MLStub(channel)

    payload = Struct()
    payload.update({
        "features": features
    })
    request = grpc_pb2.MLRequest(
        request=payload
    )

    response = stub.Predict(request)

    print("Model prediction results:", response)


if __name__ == "__main__":
    predict()

    # kubectl get service 'service name'
