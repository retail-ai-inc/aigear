class CloudBuildError(Exception):
    """Raised when a Cloud Build operation fails"""


class CloudBuildConfigError(Exception):
    """Raised when Cloud Build configuration is invalid"""


class CloudBuildAuthenticationError(Exception):
    """Raised when Cloud Build authentication fails"""


class CloudBuildTimeoutError(Exception):
    """Raised when Cloud Build operation times out""" 