import subprocess


import subprocess

def run_sh(command: list | str, inputs: str = None):
    """
    Cross-platform safe subprocess runner.
    If the command contains shell operators like | or >, runs in shell mode.
    """
    use_shell = isinstance(command, str) or any(op in command for op in ["|", ">", "&&", ";"])

    try:
        result = subprocess.run(
            command,
            input=inputs,
            text=True,
            capture_output=True,
            shell=use_shell,
            timeout=30,
            encoding="utf-8"
        )
        return (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return f"ERROR: Command({command}) execution timeout."
