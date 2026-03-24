import os

# ---------------------------------------------------------------------------
# Number of threads per framework — override via INFERENCE_NUM_THREADS env var
# ---------------------------------------------------------------------------
_N = int(os.environ.get("INFERENCE_NUM_THREADS", "1"))

# ---------------------------------------------------------------------------
# Set environment variables immediately on import
# Must happen before numpy / torch / tensorflow / sklearn are imported
# ---------------------------------------------------------------------------
os.environ["OMP_NUM_THREADS"] = str(_N)       # OpenMP (sklearn, CatBoost, XGBoost, LightGBM)
os.environ["MKL_NUM_THREADS"] = str(_N)       # Intel MKL (numpy, sklearn-intelex)
os.environ["OPENBLAS_NUM_THREADS"] = str(_N)  # OpenBLAS (numpy on non-Intel)
os.environ["NUMEXPR_NUM_THREADS"] = str(_N)   # NumExpr (pandas eval)
os.environ["VECLIB_MAXIMUM_THREADS"] = str(_N)# Apple Accelerate (macOS)
os.environ["BLIS_NUM_THREADS"] = str(_N)      # BLIS BLAS


def configure_frameworks(n: int = _N) -> None:
    """
    Apply framework-specific thread limits via Python APIs.

    Call this after all ML framework imports are complete.
    Safe to call even if frameworks are not installed.

    Args:
        n: Number of threads (defaults to INFERENCE_NUM_THREADS env var, or 1).
    """
    _configure_torch(n)
    _configure_tensorflow(n)


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
