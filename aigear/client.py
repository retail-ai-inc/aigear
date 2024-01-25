import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from aigear.proto import grpc_pb2, grpc_pb2_grpc


class MlgrpcClient:
    def __init__(
            self,
            service_host='localhost:50051'
    ):
        """
        setting grpc client.

        service_host: str = 'localhost:50051'
        """
        self.service_host = service_host

    def predict(self, request):
        stub = self.channel()
        request_body = self.encode_request(request)
        response_data = stub.Predict(request_body)
        response = self.decode_response(response_data)
        return response

    def channel(self):
        channel = grpc.insecure_channel(
            target=self.service_host,
        )
        stub = grpc_pb2_grpc.MLStub(channel)
        return stub

    def encode_request(self, request):
        request_data = struct_pb2.Struct()
        request_data.update(request)
        response = grpc_pb2.MLRequest()
        response.request.update(request)
        return response

    def decode_response(self, response):
        response = MessageToDict(response)
        return response['response']['response']
