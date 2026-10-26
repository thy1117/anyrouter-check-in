"""The one-shot www retry must not touch unrelated accounts or challenges."""

import pytest

from utils.config import AccountConfig

TARGETS = ('1125', '5237', '3069', '9308', 'hotmail', '31', '24', '37', '38')


def accounts():
	return [
		AccountConfig(cookies=None, name=f'NexaVlinks-{suffix}', provider='nexavlinks', access_token='test')
		for suffix in TARGETS
	]


def test_exact_failed_accounts_only():
	from scripts.retry_nexa_www import select_accounts

	rows = accounts()
	unrelated = AccountConfig(cookies=None, name='AIAIAI-thy1117', provider='aiaiai', access_token='test')
	assert select_accounts(rows + [unrelated]) == rows
	for invalid in (rows[:-1], rows + rows[:1]):
		with pytest.raises(ValueError):
			select_accounts(invalid)
	rows[0].provider = 'wrong'
	with pytest.raises(ValueError):
		select_accounts(rows)


def test_retry_only_uses_get_status_reader(monkeypatch, tmp_path):
	from scripts import retry_nexa_www

	rows = accounts()
	monkeypatch.chdir(tmp_path)
	monkeypatch.setattr(retry_nexa_www, 'load_accounts_config', lambda: rows)
	seen = []

	def read(account, provider):
		seen.append((account.name, provider.domain, provider.token_state_domain))
		return {'account': account.name, 'status_read_ok': True, 'checked_in_today': True}

	monkeypatch.setattr(retry_nexa_www, 'read_status', read)
	assert retry_nexa_www.run() == 0
	assert len(seen) == 9
	assert {row[1] for row in seen} == {'https://www.nexavlinks.com'}
	assert {row[2] for row in seen} == {'https://asia.nexavlinks.com'}
	assert (tmp_path / 'nexa-www-results.json').exists()


def test_isolated_workflow_has_only_get_diagnostic():
	from pathlib import Path

	import yaml

	workflow = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	assert set(workflow['jobs']) == {'nexa-www-status'}
	job = workflow['jobs']['nexa-www-status']
	assert job['permissions'] == {'contents': 'read'}
	assert workflow['concurrency']['cancel-in-progress'] is False
	step = next(step for step in job['steps'] if 'scripts.retry_nexa_www' in step.get('run', ''))
	assert not any('TELEGRAM' in key or 'PROXY' in key for key in step['env'])
	assert {key for key in step['env'] if key.startswith('EXTRA_ACCOUNTS_')} == {
		f'EXTRA_ACCOUNTS_{number}' for number in [64, 65, *range(83, 98)]
	}
