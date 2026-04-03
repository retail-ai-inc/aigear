import platform
import re
import subprocess
import sys

# Windows cmd.exe tmpfile redirect artifacts (e.g. D:\...\tmpfile)
_WIN_TMPFILE_RE = re.compile(r'^[A-Za-z]:[/\\].*tmpfile\s*$')


def _clean_output(text: str) -> str:
    """Remove Windows cmd.exe shell redirect noise from subprocess output."""
    if platform.system() != "Windows":
        return text
    result = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip('\r\n')
        if _WIN_TMPFILE_RE.match(stripped):
            continue
        # GBK error messages decoded as UTF-8 produce mostly replacement chars
        if stripped and stripped.count('\ufffd') > len(stripped) * 0.3:
            continue
        result.append(line)
    return ''.join(result)


def run_sh(
    command: list,
    inputs: str = None,
    timeout: int = 30,
):
    use_shell = (platform.system() == "Windows")
    try:
        input_bytes = inputs.encode("utf-8") if inputs else None
        result = subprocess.run(
            command,
            input=input_bytes,
            capture_output=True,
            shell=use_shell,
            timeout=timeout,
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        return _clean_output(stdout + stderr)
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
        bufsize=0,
    )

    try:
        if inputs is not None:
            proc.stdin.write(inputs.encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()

        if platform.system() == "Windows":
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                print(chunk.decode("utf-8", errors="replace"), end="")
                sys.stdout.flush()
        else:
            for line in proc.stdout:
                print(line.decode("utf-8", errors="replace"), end="")
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
