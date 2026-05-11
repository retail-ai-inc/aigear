import subprocess
from unittest.mock import patch, MagicMock

import pytest

from aigear.common.sh import _clean_output, run_sh


@patch("aigear.common.sh.platform.system", return_value="Linux")
def test_clean_output_on_non_windows_returns_unchanged(mock_sys):
    result = _clean_output("hello\nworld\n")
    assert result == "hello\nworld\n"


@patch("aigear.common.sh.platform.system", return_value="Windows")
def test_clean_output_on_windows_removes_tmpfile_lines(mock_sys):
    text = "normal output\nC:\\Users\\sometmpfile\n"
    result = _clean_output(text)
    assert "normal output" in result
    assert "sometmpfile" not in result


@patch("aigear.common.sh.platform.system", return_value="Windows")
def test_clean_output_on_windows_keeps_non_tmpfile_lines(mock_sys):
    text = "line one\nline two\n"
    result = _clean_output(text)
    assert "line one" in result
    assert "line two" in result


@patch("aigear.common.sh.platform.system", return_value="Windows")
def test_clean_output_on_windows_removes_high_replacement_char_lines(mock_sys):
    # >30% replacement chars triggers removal
    bad_line = "�" * 10 + "a"  # 10/11 ≈ 90.9%
    text = f"{bad_line}\nnormal\n"
    result = _clean_output(text)
    assert "normal" in result
    assert bad_line not in result


@patch("aigear.common.sh.platform.system", return_value="Windows")
def test_clean_output_keeps_lines_with_few_replacement_chars(mock_sys):
    # <30% replacement chars should be kept
    ok_line = "�" + "a" * 10  # 1/11 ≈ 9%
    text = f"{ok_line}\n"
    result = _clean_output(text)
    assert ok_line in result


@patch("aigear.common.sh.subprocess.run")
def test_run_sh_returns_combined_stdout_and_stderr(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = b"hello "
    mock_result.stderr = b"world"
    mock_result.returncode = 0
    mock_run.return_value = mock_result

    result = run_sh(["echo", "hello"])
    assert "hello" in result
    assert "world" in result


@patch("aigear.common.sh.subprocess.run")
def test_run_sh_raises_on_nonzero_exit_when_check_true(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = b""
    mock_result.stderr = b"command failed"
    mock_result.returncode = 1
    mock_run.return_value = mock_result

    with pytest.raises(RuntimeError, match="Command failed"):
        run_sh(["bad-command"], check=True)


@patch("aigear.common.sh.subprocess.run")
def test_run_sh_does_not_raise_on_nonzero_exit_without_check(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = b""
    mock_result.stderr = b"some error"
    mock_result.returncode = 1
    mock_run.return_value = mock_result

    result = run_sh(["bad-command"])
    assert "some error" in result


@patch("aigear.common.sh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=30))
def test_run_sh_returns_timeout_message_on_timeout(mock_run):
    result = run_sh(["slow-command"])
    assert "timeout" in result.lower()


@patch("aigear.common.sh.subprocess.run")
def test_run_sh_encodes_inputs_as_utf8(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = b""
    mock_result.stderr = b""
    mock_result.returncode = 0
    mock_run.return_value = mock_result

    run_sh(["cat"], inputs="hello")
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["input"] == b"hello"


@patch("aigear.common.sh.subprocess.run")
def test_run_sh_passes_none_input_when_no_inputs(mock_run):
    mock_result = MagicMock()
    mock_result.stdout = b""
    mock_result.stderr = b""
    mock_result.returncode = 0
    mock_run.return_value = mock_result

    run_sh(["ls"])
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["input"] is None
