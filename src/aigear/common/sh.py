import subprocess


def run_sh(
    command: list,
    inputs: str = None,
):
    result = subprocess.run(
        command,
        input=inputs,
        text=True,
        capture_output=True,
        shell=True,
    )
    stderr = result.stderr
    if stderr:
        return stderr
    else:
        return result.stdout
