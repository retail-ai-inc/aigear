import os
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Number of threads per framework — override via INFERENCE_NUM_THREADS env var
# ---------------------------------------------------------------------------
_N = os.environ.get("INFERENCE_NUM_THREADS", "1")

# ---------------------------------------------------------------------------
# Set environment variables immediately on import
# Must happen before numpy / torch / tensorflow / sklearn are imported
# ---------------------------------------------------------------------------
def set_ml_thread_env_vars(n: str = _N) -> None:
    """
    Set thread-limit environment variables for all ML/BLAS backends.

    Must be called before numpy / torch / tensorflow / sklearn are imported.
    """

    os.environ["OMP_NUM_THREADS"] = n        # OpenMP (sklearn, CatBoost, XGBoost, LightGBM)
    os.environ["MKL_NUM_THREADS"] = n        # Intel MKL (numpy, sklearn-intelex)
    os.environ["OPENBLAS_NUM_THREADS"] = n   # OpenBLAS (numpy on non-Intel)
    os.environ["NUMEXPR_NUM_THREADS"] = n    # NumExpr (pandas eval)
    os.environ["VECLIB_MAXIMUM_THREADS"] = n # Apple Accelerate (macOS)
    os.environ["BLIS_NUM_THREADS"] = n       # BLIS BLAS


def configure_framework_threads(n: str = _N) -> None:
    """
    Apply framework-specific thread limits via Python APIs.

    Call this after all ML framework imports are complete.
    Safe to call even if frameworks are not installed.

    Args:
        n: Number of threads (defaults to INFERENCE_NUM_THREADS env var, or 1).
    """
    n_int = int(n)
    _configure_torch(n_int)
    _configure_tensorflow(n_int)


@contextmanager
def ml_thread_scope(enabled: bool = True):
    """
    Context manager that sets ML thread env vars before the block
    and applies framework-level thread limits after.

    Usage::

        with ml_thread_scope(disable_omp):
            model = load_model(...)
    """
    if enabled:
        set_ml_thread_env_vars()
    yield
    if enabled:
        configure_framework_threads()


def _configure_torch(n: int) -> None:
    # Only configure if torch is already imported — avoids triggering slow torch init
    import sys
    if "torch" not in sys.modules:
        return
    try:
        import torch
        torch.set_num_threads(n)
        torch.set_num_interop_threads(n)
    except Exception:
        pass


def _configure_tensorflow(n: int) -> None:
    # Only configure if TF is already imported — avoids triggering slow TF runtime init
    import sys
    if "tensorflow" not in sys.modules:
        return
    try:
        import tensorflow as tf
        tf.config.threading.set_intra_op_parallelism_threads(n)
        tf.config.threading.set_inter_op_parallelism_threads(n)
    except Exception:
        pass
