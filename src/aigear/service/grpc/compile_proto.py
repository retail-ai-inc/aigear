import grpc_tools
from pathlib import Path
from grpc_tools import protoc
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

def compile_proto():
    base_path = Path(__file__).resolve().parent
    print(str(base_path / "grpc.protos"))

    proto_path = base_path / "protos"
    pb2 = proto_path / "grpc_pb2.py"
    pb2_grpc = proto_path / "grpc_pb2_grpc.py"
    if pb2.exists() and pb2_grpc.exists():
        logger.info("gRPC protos already compiled, skipping.")
        return

    logger.info("Compiling gRPC protos ...")
    include_google = Path(grpc_tools.__file__).parent / "_proto"
    args = [
        "",
        f"-I{base_path}",
        f"-I{include_google}",
        f"--python_out={proto_path}",
        f"--grpc_python_out={proto_path}",
        str(base_path / "grpc.proto"),
    ]
    result = protoc.main(args)
    if result != 0:
        logger.error(f"gRPC protos compilation failed with code {result}")
    else:
        logger.info("Compiled gRPC protos successfully!")

if __name__ == "__main__":
    compile_proto()