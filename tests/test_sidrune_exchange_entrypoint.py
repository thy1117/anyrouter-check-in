import json
from pathlib import Path

import pytest

from scripts.exchange_sidrune import SIDRUNE_SLOTS, select_accounts


def account_slots():
	return {
		slot: json.dumps([{'provider': 'sidrune', 'name': f'test-{index}', 'access_token': f'private-{index}'}])
		for index, slot in enumerate(SIDRUNE_SLOTS)
	}


def test_only_six_sidrune_slots_are_loaded():
	slots = account_slots()
	slots['EXTRA_ACCOUNTS_13'] = json.dumps([{'provider': 'xiaobai', 'name': 'other'}])
	accounts = select_accounts(slots)
	assert len(accounts) == 6
	assert all(account.provider == 'sidrune' for account in accounts)
	assert [account.access_token for account in accounts] == [f'private-{index}' for index in range(6)]


@pytest.mark.parametrize(
	'value',
	['', 'private-invalid-json', '{}', '[]', '[{}]', '[{"provider":"xiaobai"}]'],
)
def test_invalid_secret_fails_before_any_account_is_run(value):
	slots = account_slots()
	slots[SIDRUNE_SLOTS[-1]] = value
	with pytest.raises(ValueError) as error:
		select_accounts(slots)
	assert 'private-' not in str(error.value)


def test_duplicate_accounts_are_rejected():
	slots = account_slots()
	slots[SIDRUNE_SLOTS[-1]] = slots[SIDRUNE_SLOTS[0]]
	with pytest.raises(ValueError, match='duplicate'):
		select_accounts(slots)


def test_exchange_entrypoint_cannot_run_checkin_or_other_sites(monkeypatch, capsys):
	import scripts.exchange_sidrune as entrypoint

	for slot, value in account_slots().items():
		monkeypatch.setenv(slot, value)
	requests = []

	def exchange(account, name, provider, *, exchange_only):
		assert exchange_only is True and account.provider == provider.name == 'sidrune'
		requests.append(name)
		return True, {'success': True, 'quota': 1.0}, {'success': True, 'quota': 1.5}

	monkeypatch.setattr(entrypoint, 'run_bearer_check_in', exchange)
	assert entrypoint.main() == 0
	assert requests == [f'test-{index}' for index in range(6)]
	output = capsys.readouterr().out
	assert 'private-' not in output
	assert output.count('"wallet_change": 0.5') == 6


def test_exchange_workflow_loads_no_other_sites_or_notifications():
	text = Path('.github/workflows/checkin.yml').read_text()
	job = text.split('\n  sidrune-exchange-test:', 1)[1].split('\n  xiaobai-test:', 1)[0]
	assert 'run: uv run python -m scripts.exchange_sidrune' in job
	assert 'environment: production' in job and 'contents: write' in job
	assert 'checkin-session-state-${{ github.repository }}' in text
	assert 'run checkin.py' not in job and 'TELEGRAM_BOT_TOKEN' not in job
	assert 'PROXY_' not in job and 'ANYROUTER_ACCOUNTS' not in job
	for slot in SIDRUNE_SLOTS:
		assert f'{slot}: ${{{{ secrets.{slot} }}}}' in job
	assert 'EXTRA_ACCOUNTS_13:' not in job
	assert 'EXTRA_ACCOUNTS_120:' not in job
