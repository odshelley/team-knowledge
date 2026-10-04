import subprocess
import sys

import teamknowledge


def test_version_constant():
    assert teamknowledge.__version__ == "0.1.0"


def test_tk_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "teamknowledge.cli", "--version"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "tk 0.1.0"
