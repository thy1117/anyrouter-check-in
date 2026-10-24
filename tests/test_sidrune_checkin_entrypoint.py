import json
from pathlib import Path

import pytest
import yaml

from scripts.check_sidrune import CHECKIN_SLOTS
from scripts.exchange_sidrune import select_accounts


def account_slots():
	return {
		slot: json.dumps([{'provider': 'sidrune', 'name': f'account-{index}', 'access_token': f'private-{index}'}])
		for index, slot in enumerate(CHECKIN_SLOTS)
	}


def test_checkin_entrypoint_only_runs_all_eighteen_sidrune_accounts(monkeypatch, capsys):
	import scripts.check_sidrune as entrypoint

	for slot, value in account_slots().items():
		monkeypatch.setenv(slot, value)
	monkeypatch.setenv('EXTRA_ACCOUNTS_13', '[{"provider":"nexa","name":"not-selected"}]')
	requests = []

	def checkin(account, name, provider, *, checkin_only):
		assert checkin_only is True and account.provider == provider.name == 'sidrune'
		requests.append(name)
		return True, None, {'sidrune_checkin': {'checked_in_today': True, 'already_checked_in': False}}

	monkeypatch.setattr(entrypoint, 'run_bearer_check_in', checkin)
	assert entrypoint.main() == 0
	assert requests == [f'account-{index}' for index in range(18)]
	output = capsys.readouterr().out
	assert 'private-' not in output and output.count('"status": "checked_in"') == 18


@pytest.mark.parametrize('value', ['', 'private-invalid-json', '{}', '[]', '[{}]', '[{"provider":"nexa"}]'])
def test_invalid_checkin_slot_fails_before_running_any_account(value):
	slots = account_slots()
	slots[CHECKIN_SLOTS[-1]] = value
	with pytest.raises(ValueError) as error:
		select_accounts(slots, CHECKIN_SLOTS)
	assert 'private-' not in str(error.value)


def test_checkin_workflow_uses_only_sidrune_secrets_and_no_notifications():
	config = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	job = config['jobs']['sidrune-checkin-test']
	assert job['environment'] == 'production' and job['permissions'] == {'contents': 'write'}
	step = next(step for step in job['steps'] if step.get('run') == 'uv run --no-dev python -m scripts.check_sidrune')
	assert set(step['env']) == {
		*CHECKIN_SLOTS,
		'PYTHONUNBUFFERED',
		'CHECKIN_TIMEZONE',
		'XIAOBAI_TOKEN_STATE_KEY',
		'XIAOBAI_STATE_GITHUB_TOKEN',
	}
	for slot in CHECKIN_SLOTS:
		assert step['env'][slot] == '${{ secrets.' + slot + ' }}'
