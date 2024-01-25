
python -m grpc_tools.protoc -I ./proto --python_out=./grpc/pyproto --grpc_python_out=./grpc/pyproto grpc.proto

protoc -I . --go-grpc_out=grpc/goproto --go_out=grpc/goproto proto/grpc.proto
