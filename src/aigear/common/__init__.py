def __getattr__(name: str):
    if name in ("run_sh", "run_sh_stream"):
        from aigear.common.sh import run_sh, run_sh_stream

        globals()["run_sh"] = run_sh
        globals()["run_sh_stream"] = run_sh_stream
        return globals()[name]
    raise AttributeError(f"module 'aigear.common' has no attribute {name!r}")


__all__ = ["run_sh", "run_sh_stream"]
