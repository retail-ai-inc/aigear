import time
import signal
import contextlib
import socket
import argparse


def wait_until_closed(server):
    """
    Wait indefinitely until receiving a signal to shut down the service.
    """

    # SIGTERM info
    def sigterm_handler(signum, frame):
        server.stop(grace=None)

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        while True:
            time.sleep(60 * 60)
    except KeyboardInterrupt:
        sigterm_handler(signal.SIGTERM, None)


@contextlib.contextmanager
def reserve_port(port):
    """
    Find and reserve a port for all subprocesses to use
    """
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    if sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0:
        raise RuntimeError("Failed to set SO_REUSEPORT.")
    sock.bind(("", port))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def get_argument(default_tag=""):
    # Arg
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tag', default=default_tag,
                        help='Deploy gRPC services by tag code, tag is also a version.')
    args = parser.parse_args()
    return args
