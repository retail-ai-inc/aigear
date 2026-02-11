import subprocess
import platform
import sys


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

def run_sh_stream(command: list, inputs: str = None):
    use_shell = (platform.system() == "Windows")
    proc = subprocess.Popen(
        command,
        shell=use_shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    if inputs is not None:
        proc.stdin.write(inputs)
        proc.stdin.flush()

    for line in proc.stdout:
        print(line, end="")
        sys.stdout.flush()

    returncode = proc.wait()
    return returncode
