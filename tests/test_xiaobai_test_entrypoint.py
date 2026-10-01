import json

import pytest

from scripts.check_xiaobai import select_account


def test_exact_name_selects_only_one_xiaobai():
	slots = {
		'one': json.dumps(
			[
				{'provider': 'xiaobai', 'name': 'target', 'access_token': 'a'},
				{'provider': 'xiaobai', 'name': 'other', 'access_token': 'b'},
				{'provider': 'anyrouter', 'name': 'target', 'access_token': 'c'},
			]
		)
	}
	result = select_account('target', slots)
	assert result.provider == 'xiaobai'
	assert result.access_token == 'a'


@pytest.mark.parametrize(
	'slots',
	[
		{'one': '[]'},
		{'one': json.dumps([{'provider': 'xiaobai', 'name': 'target'}] * 2)},
		{'one': json.dumps({'provider': 'xiaobai', 'name': 'target'})},
	],
)
def test_missing_ambiguous_or_malformed_input_fails_closed(slots):
	with pytest.raises(ValueError):
		select_account('target', slots)


def test_config_only_identifies_exact_secret_without_any_site_or_state_requests(monkeypatch, capsys):
	import scripts.check_xiaobai as entrypoint

	monkeypatch.setenv('XIAOBAI_TEST_ACCOUNT', 'target')
	monkeypatch.setenv('XIAOBAI_CONFIG_ONLY', 'true')
	# Even a request for check-in must be overridden by configuration-only mode.
	monkeypatch.setenv('XIAOBAI_STATUS_ONLY', 'false')
	monkeypatch.setenv('EXTRA_ACCOUNTS_16', '[]')
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_13',
		json.dumps(
			[
				{
					'provider': 'xiaobai',
					'name': 'target',
					'access_token': 'private-access-value',
					'refresh_token': 'private-refresh-value',
				},
				{'provider': 'anyrouter', 'name': 'target', 'access_token': 'other-private-value'},
			]
		),
	)

	def forbidden(*args, **kwargs):
		pytest.fail('Configuration-only lookup must not authenticate, refresh, check in or load provider state')

	monkeypatch.setattr(entrypoint, 'run_xiaobai_check_in', forbidden)
	monkeypatch.setattr(entrypoint.AppConfig, 'load_from_env', forbidden)
	assert entrypoint.main() == 0
	output = capsys.readouterr().out
	assert 'production Secret EXTRA_ACCOUNTS_13' in output
	assert 'no authentication, refresh or check-in attempted' in output
	assert 'private-' not in output


def test_source_lookup_rejects_duplicate_names_across_secrets():
	from scripts.check_xiaobai import select_account_with_source

	value = json.dumps([{'provider': 'xiaobai', 'name': 'target', 'access_token': 'a'}])
	with pytest.raises(ValueError, match='exactly one'):
		select_account_with_source('target', {'EXTRA_ACCOUNTS_16': value, 'EXTRA_ACCOUNTS_13': value})


@pytest.mark.parametrize('slot_number', [42, 43, 44, 45])
def test_config_only_finds_later_slots_without_running_any_account(monkeypatch, capsys, slot_number):
	import scripts.check_xiaobai as entrypoint

	for slot in entrypoint.XIAOBAI_TEST_SLOTS:
		monkeypatch.delenv(slot, raising=False)
	monkeypatch.setenv('XIAOBAI_TEST_ACCOUNT', '小白Code-5237')
	monkeypatch.setenv('XIAOBAI_CONFIG_ONLY', 'true')
	monkeypatch.setenv('XIAOBAI_STATUS_ONLY', 'false')
	monkeypatch.setenv(
		f'EXTRA_ACCOUNTS_{slot_number}',
		json.dumps(
			[
				{'provider': 'xiaobai', 'name': '小白Code-5237', 'access_token': 'private-target'},
				{'provider': 'xiaobai', 'name': 'other', 'access_token': 'private-other'},
			]
		),
	)

	def forbidden(*args, **kwargs):
		pytest.fail('Config lookup must not load state or run any account')

	monkeypatch.setattr(entrypoint, 'run_xiaobai_check_in', forbidden)
	monkeypatch.setattr(entrypoint.AppConfig, 'load_from_env', forbidden)
	assert entrypoint.main() == 0
	output = capsys.readouterr().out
	assert f'production Secret EXTRA_ACCOUNTS_{slot_number}' in output
	assert 'private-' not in output


def test_isolated_workflow_wires_exactly_the_supported_xiaobai_slots():
	import re
	from pathlib import Path

	from scripts.check_xiaobai import XIAOBAI_TEST_SLOTS

	workflow = Path('.github/workflows/checkin.yml').read_text().split('  xiaobai-test:', 1)[1]
	wired = re.findall(r'        (EXTRA_ACCOUNTS_\d+):', workflow)
	assert set(wired) == set(XIAOBAI_TEST_SLOTS)
	for slot in XIAOBAI_TEST_SLOTS:
		assert f'{slot}: ${{{{ secrets.{slot} }}}}' in workflow
