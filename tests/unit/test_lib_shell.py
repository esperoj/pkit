import sys

import pytest

from pkit.lib import shell


def test_run_captures_stdout():
    result = shell.run([sys.executable, "-c", "print('hi')"])
    assert result.stdout.strip() == "hi"
    assert result.returncode == 0


def test_run_raises_with_returncode_and_stderr():
    with pytest.raises(shell.ShellError) as exc:
        shell.run([sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"])
    assert exc.value.returncode == 3
    assert "bad" in exc.value.stderr


def test_run_missing_binary():
    with pytest.raises(shell.CommandNotFound):
        shell.run(["pkit-definitely-not-installed"])


def test_run_rejects_empty_args():
    with pytest.raises(shell.ShellError):
        shell.run([])
