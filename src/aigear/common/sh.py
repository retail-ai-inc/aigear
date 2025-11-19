import subprocess
import platform


def run_sh(
    command: list,
    inputs: str = None,
):
    if platform.system() == "Windows":
        use_shell = True
    else:
        use_shell = False
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
        return f"Error: Command({command}) execution timeout."
