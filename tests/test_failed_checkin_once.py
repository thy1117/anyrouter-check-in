"""The manual branch selects exact accounts and stops at human verification."""

from pathlib import Path

import pytest
import yaml

from utils.config import AccountConfig


def test_selection_rejects_missing_duplicate_and_wrong_accounts():
	from scripts.failed_checkin_once import select_accounts

	accounts = [
		AccountConfig(cookies=None, name=name, provider=provider, access_token='dummy', api_user=str(uid))
		for name, provider, uid in (
			('NexaVlinks-27', 'nexavlinks', 6940),
			('NexaVlinks-38', 'nexavlinks', 6946),
			('Nianhua-thy1119', 'nianhua', 8108),
		)
	]
	assert [a.name for a in select_accounts(accounts)] == [a.name for a in accounts]
	for invalid in (accounts[:2], accounts + accounts[:1]):
		with pytest.raises(ValueError):
			select_accounts(invalid)
	accounts[-1].provider = 'wrong'
	with pytest.raises(ValueError):
		select_accounts(accounts)


@pytest.mark.parametrize('checked,phase', [(True, 'confirmed'), (False, 'human_verification_required')])
def test_current_status_never_invokes_challenge_solver(checked, phase):
	from scripts.failed_checkin_once import classify

	result = classify('NexaVlinks-27', True, checked, 6940, eligible=True)
	assert result['phase'] == phase
	assert result['check_in_submitted'] is False
	assert result['checked_in_today'] is checked


def test_identity_mismatch_and_failed_reads_fail_closed():
	from scripts.failed_checkin_once import classify

	assert classify('NexaVlinks-27', True, True, 6946)['phase'] == 'identity_mismatch'
	assert classify('NexaVlinks-27', False, False, None)['phase'] == 'status_read_failed'


def test_workflow_is_branch_only_exact_scope_without_solver_or_notifier():
	config = yaml.safe_load(Path('.github/workflows/checkin.yml').read_text())
	assert set(config['jobs']) == {'failed-checkin-once'}
	job = config['jobs']['failed-checkin-once']
	assert 'run/failed-checkin-20261023' in job['if']
	assert job['permissions'] == {'contents': 'read'}
	assert config['concurrency']['group'] == 'checkin-session-state-${{ github.repository }}'
	step = next(s for s in job['steps'] if 'scripts.failed_checkin_once' in s.get('run', ''))
	assert not any('TELEGRAM' in k or 'PROXY' in k for k in step['env'])
	source = Path('scripts/failed_checkin_once.py').read_text()
	assert 'status_only=True' in source
	assert 'solve_' not in source and '.post(' not in source
