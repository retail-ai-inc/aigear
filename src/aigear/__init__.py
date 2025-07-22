from aigear.project import Project
from aigear._version import __version__
from aigear.common import Logging


aigear_logger = Logging(log_name='aigear_logging').console_logging()

__all__ = []

__all__.extend(
    [
        Project,
        __version__,
    ]
)
