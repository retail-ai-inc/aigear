python -m grpc_tools.protoc -I ./proto --python_out=./grpc/proto --grpc_python_out=./grpc/proto grpc.proto

protoc -I . --go-grpc_out=grpc/goproto --go_out=grpc/goproto proto/grpc.proto
