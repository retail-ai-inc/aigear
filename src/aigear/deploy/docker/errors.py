class BuildError(Exception):
    """Raised when a Docker build fails"""


class PushError(Exception):
    """Raised when a Docker image push fails"""
