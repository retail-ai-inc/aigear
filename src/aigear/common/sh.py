import platform
import subprocess
import sys


def run_sh(
    command: list,
    inputs: str = None,
    timeout: int=30,
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
            timeout=timeout,
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
        stdin=subprocess.PIPE if inputs is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    try:
        if inputs is not None:
            proc.stdin.write(inputs)
            proc.stdin.flush()
            proc.stdin.close()

        for line in proc.stdout:
            print(line, end="")
            sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n[Interrupted] Process terminated by user.")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise

    returncode = proc.wait()
    return returncode
