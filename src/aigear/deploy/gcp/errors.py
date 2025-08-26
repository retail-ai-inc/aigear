from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class CloudBuildError(Exception):
    """Raised when Cloud Build operations fail"""
    
    def __init__(self, message: str, error_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}


class CloudBuildConfigError(CloudBuildError):
    """Raised when Cloud Build configuration is invalid"""
    pass


class CloudBuildAuthenticationError(CloudBuildError):
    """Raised when Cloud Build authentication fails"""
    pass


class CloudBuildTimeoutError(CloudBuildError):
    """Raised when Cloud Build operations timeout"""
    pass


class CloudBuildPermissionError(CloudBuildError):
    """Raised when insufficient permissions"""
    pass


class CloudBuildResourceError(CloudBuildError):
    """Raised when resource-related errors occur"""
    pass


# Chain of Responsibility Pattern: Error handlers
class ErrorHandler(ABC):
    """Abstract base class for error handlers"""
    
    def __init__(self):
        self._next_handler: Optional['ErrorHandler'] = None
    
    def set_next(self, handler: 'ErrorHandler') -> 'ErrorHandler':
        self._next_handler = handler
        return handler
    
    @abstractmethod
    def can_handle(self, error: Exception) -> bool:
        """Determine if this handler can process the error"""
        pass
    
    @abstractmethod
    def handle(self, error: Exception) -> CloudBuildError:
        """Handle the error"""
        pass
    
    def process(self, error: Exception) -> CloudBuildError:
        """Process error or pass to next handler"""
        if self.can_handle(error):
            return self.handle(error)
        elif self._next_handler:
            return self._next_handler.process(error)
        else:
            # Default handling
            return CloudBuildError(f"Unhandled error: {error}")


class AuthenticationErrorHandler(ErrorHandler):
    """Authentication error handler"""
    
    def can_handle(self, error: Exception) -> bool:
        from google.auth.exceptions import DefaultCredentialsError
        error_msg = str(error).lower()
        return (
            isinstance(error, DefaultCredentialsError) or
            'credential' in error_msg or
            'authentication' in error_msg or
            'permission denied' in error_msg
        )
    
    def handle(self, error: Exception) -> CloudBuildError:
        return CloudBuildAuthenticationError(
            f"Authentication failed: {error}\n"
            f"Solutions:\n"
            f"1. Run 'gcloud auth application-default login'\n"
            f"2. Set GOOGLE_APPLICATION_CREDENTIALS environment variable\n"
            f"3. Verify IAM permissions (Cloud Build Editor role required)",
            error_code="AUTH_FAILED",
            details={"original_error": str(error)}
        )


class PermissionErrorHandler(ErrorHandler):
    """Permission error handler"""
    
    def can_handle(self, error: Exception) -> bool:
        error_msg = str(error).lower()
        return (
            'permission' in error_msg or
            'access denied' in error_msg or
            'forbidden' in error_msg or
            'unauthorized' in error_msg or
            '403' in error_msg
        )
    
    def handle(self, error: Exception) -> CloudBuildError:
        return CloudBuildPermissionError(
            f"Permission denied: {error}\n"
            f"Required IAM roles:\n"
            f"1. Cloud Build Editor\n"
            f"2. Source Repository Administrator (for GitHub integration)\n"
            f"3. Storage Admin (for GCS operations)",
            error_code="PERMISSION_DENIED",
            details={"original_error": str(error)}
        )


class ResourceErrorHandler(ErrorHandler):
    """Resource error handler"""
    
    def can_handle(self, error: Exception) -> bool:
        error_msg = str(error).lower()
        return (
            'not found' in error_msg or
            'does not exist' in error_msg or
            'resource' in error_msg or
            'bucket' in error_msg or
            'repository' in error_msg or
            '404' in error_msg
        )
    
    def handle(self, error: Exception) -> CloudBuildError:
        return CloudBuildResourceError(
            f"Resource error: {error}\n"
            f"Check that:\n"
            f"1. Project ID is correct\n"
            f"2. Resources exist and are accessible\n"
            f"3. Repository URL is valid (for GitHub builds)",
            error_code="RESOURCE_ERROR",
            details={"original_error": str(error)}
        )


class TimeoutErrorHandler(ErrorHandler):
    """Timeout error handler"""
    
    def can_handle(self, error: Exception) -> bool:
        error_msg = str(error).lower()
        return (
            'timeout' in error_msg or
            'deadline' in error_msg or
            'timed out' in error_msg
        )
    
    def handle(self, error: Exception) -> CloudBuildError:
        return CloudBuildTimeoutError(
            f"Operation timed out: {error}\n"
            f"Solutions:\n"
            f"1. Increase timeout value\n"
            f"2. Check network connectivity\n"
            f"3. Verify build complexity is reasonable",
            error_code="TIMEOUT",
            details={"original_error": str(error)}
        )


class ConfigErrorHandler(ErrorHandler):
    """Configuration error handler"""
    
    def can_handle(self, error: Exception) -> bool:
        error_msg = str(error).lower()
        return (
            'dockerfile' in error_msg or
            'invalid' in error_msg or
            'configuration' in error_msg or
            'format' in error_msg or
            'syntax' in error_msg
        )
    
    def handle(self, error: Exception) -> CloudBuildError:
        return CloudBuildConfigError(
            f"Configuration error: {error}\n"
            f"Check:\n"
            f"1. Dockerfile exists and is valid\n"
            f"2. Build configuration is correct\n"
            f"3. All required parameters are provided",
            error_code="CONFIG_ERROR",
            details={"original_error": str(error)}
        )


class CloudBuildErrorProcessor:
    """Manager for error handler chain"""
    
    def __init__(self):
        # Build responsibility chain
        self._handler_chain = AuthenticationErrorHandler()
        self._handler_chain.set_next(PermissionErrorHandler()) \
                          .set_next(ResourceErrorHandler()) \
                          .set_next(TimeoutErrorHandler()) \
                          .set_next(ConfigErrorHandler())
    
    def process_error(self, error: Exception) -> CloudBuildError:
        """Process error and return appropriate exception type"""
        if isinstance(error, CloudBuildError):
            return error
        return self._handler_chain.process(error)


# Global error processor instance
error_processor = CloudBuildErrorProcessor() 