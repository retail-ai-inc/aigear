import time
import signal
from contextlib import contextmanager
import socket


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


@contextmanager
def reserve_port(port):
    """
    Find and reserve a port for all subprocesses to use
    """
    if socket.has_ipv6:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        if sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) == 0:
            raise RuntimeError("Failed to set SO_REUSEPORT.")
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()
