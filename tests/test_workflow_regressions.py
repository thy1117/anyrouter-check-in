import os
import re
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_caches_use_valid_expressions_and_save_after_partial_failure():
	text = (ROOT / '.github/workflows/checkin.yml').read_text()
	assert not re.search(r'\$\{(?!\{)\s*(github|runner)\.', text)
	for ident in ('balance-cache', 'browser-cache', 'proxy-cache'):
		assert f'id: {ident}' in text
		assert f"!cancelled() && steps.{ident}.outcome == 'success'" in text
		assert f'steps.{ident}.outputs.cache-primary-key' in text
	assert text.count('uses: actions/cache/save@v5') == 3
	assert 'balance-hash-v2-' in text and 'browser-profiles-v2-' in text
	assert 'github.run_attempt' in text


def test_added_accounts_and_credentials_survive_the_repair():
	text = (ROOT / '.github/workflows/checkin.yml').read_text()
	for slot in (57, 58, 59):
		assert 'EXTRA_ACCOUNTS_' + str(slot) + ': ${{ secrets.EXTRA_ACCOUNTS_' + str(slot) + ' }}' in text
	assert 'cryptography' in (ROOT / 'pyproject.toml').read_text()
	assert 'from utils.xiaobai_token_state import' in (ROOT / 'checkin.py').read_text()


@pytest.mark.parametrize('check_id', ['ruff-lint', 'ruff-format', 'mypy', 'pytest'])
@pytest.mark.parametrize('command_exit', [0, 2])
def test_quality_checks_preserve_failure_through_tee(tmp_path, check_id, command_exit):
	text = (ROOT / '.github/workflows/pr-check.yml').read_text()
	assert 'shell: bash' in text
	block = text.split(f'id: {check_id}\n', 1)[1].split('      - name:', 1)[0]
	script = dedent(block.split('run: |\n', 1)[1])
	# Execute the actual workflow shell body, substituting only the check command.
	script, count = re.subn(r'uv run [^\n]+? \| tee', f"bash -c 'exit {command_exit}' | tee", script)
	assert count == 1
	output = tmp_path / 'output'
	env = dict(os.environ, GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(tmp_path / 'summary'))
	result = subprocess.run(
		['bash', '-e', '-o', 'pipefail', '-c', script], cwd=tmp_path, env=env, capture_output=True, text=True
	)
	assert result.returncode == (0 if command_exit == 0 else 1)
	assert ('status=success' if command_exit == 0 else 'status=failure') in output.read_text()
