import subprocess


def run_sh(
    command: list,
    inputs: str = None,
):
    try:
        result = subprocess.run(
            command,
            input=inputs,
            text=True,
            capture_output=True,
            shell=True,
            timeout=10,
            encoding="utf-8"
        )
        stderr = result.stderr
        if stderr:
            return stderr
        else:
            return result.stdout
    except subprocess.TimeoutExpired:
        return f"Error: Command({command}) execution timeout."
