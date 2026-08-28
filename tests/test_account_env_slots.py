import json

from utils.config import _account_env_names, load_accounts_config

BASE = json.dumps([{'name': 'Main', 'cookies': {'session': 'a'}, 'api_user': '1'}])


def _clear(monkeypatch):
	for name in (
		'ANYROUTER_ACCOUNTS',
		'EXTRA_ACCOUNTS',
		'EXTRA_ACCOUNTS_2',
		'EXTRA_ACCOUNTS_3',
		'EXTRA_ACCOUNTS_10',
		'EXTRA_ACCOUNTS_15',
		'EXTRA_ACCOUNTS_17',
	):
		monkeypatch.delenv(name, raising=False)


def test_env_order_is_stable(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('EXTRA_ACCOUNTS_10', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_2', '[]')

	# 数字后缀按数值排序，EXTRA_ACCOUNTS_10 不能排到 _2 前面。
	assert _account_env_names() == ['ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS', 'EXTRA_ACCOUNTS_2', 'EXTRA_ACCOUNTS_10']


def test_gorouter_slot_is_loaded_after_existing_numbered_slots(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('EXTRA_ACCOUNTS_15', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_2', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_10', '[]')

	assert _account_env_names() == [
		'ANYROUTER_ACCOUNTS',
		'EXTRA_ACCOUNTS',
		'EXTRA_ACCOUNTS_2',
		'EXTRA_ACCOUNTS_10',
		'EXTRA_ACCOUNTS_15',
	]


def test_numbered_slot_appends_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv('EXTRA_ACCOUNTS', json.dumps([{'name': 'Bearer', 'access_token': 'tok'}]))
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_2',
		json.dumps([{'name': 'FuturePPO', 'provider': 'futureppo', 'cookies': {'session': 's'}, 'api_user': '2806'}]),
	)

	accounts = load_accounts_config()
	assert [a.name for a in accounts] == ['Main', 'Bearer', 'FuturePPO']
	assert accounts[2].provider == 'futureppo'


def test_numbered_slot_can_still_override_by_name(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv('EXTRA_ACCOUNTS_2', json.dumps([{'name': 'Main', 'cookies': {'session': 'fresh'}}]))

	accounts = load_accounts_config()
	assert len(accounts) == 1
	assert accounts[0].cookies == {'session': 'fresh'}


def test_ignores_malformed_suffix(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv('EXTRA_ACCOUNTS_ABC', 'not-json')

	assert [a.name for a in load_accounts_config()] == ['Main']


def test_justwoker_slot_is_loaded_last(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('EXTRA_ACCOUNTS_17', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_2', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_15', '[]')

	assert _account_env_names() == [
		'ANYROUTER_ACCOUNTS',
		'EXTRA_ACCOUNTS',
		'EXTRA_ACCOUNTS_2',
		'EXTRA_ACCOUNTS_15',
		'EXTRA_ACCOUNTS_17',
	]
