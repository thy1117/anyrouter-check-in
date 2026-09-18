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
		'EXTRA_ACCOUNTS_18',
		'EXTRA_ACCOUNTS_34',
		'EXTRA_ACCOUNTS_35',
		'EXTRA_ACCOUNTS_36',
		'EXTRA_ACCOUNTS_37',
		'EXTRA_ACCOUNTS_38',
		'EXTRA_ACCOUNTS_39',
		'EXTRA_ACCOUNTS_40',
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


def test_tabitoken_slot_is_loaded_last(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('EXTRA_ACCOUNTS_18', '[]')
	monkeypatch.setenv('EXTRA_ACCOUNTS_2', '[]')

	assert _account_env_names() == [
		'ANYROUTER_ACCOUNTS',
		'EXTRA_ACCOUNTS',
		'EXTRA_ACCOUNTS_2',
		'EXTRA_ACCOUNTS_18',
	]


def test_gemai_slot_appends_pat_account_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_34',
		json.dumps([{'name': 'Gemai', 'provider': 'gemai', 'access_token': 'test-token', 'api_user': '12345'}]),
	)

	accounts = load_accounts_config()

	assert _account_env_names()[-1] == 'EXTRA_ACCOUNTS_34'
	assert [account.name for account in accounts] == ['Main', 'Gemai']
	assert accounts[0].cookies == {'session': 'a'}
	assert accounts[1].provider == 'gemai'
	assert accounts[1].api_user == '12345'
	assert accounts[1].access_token == 'test-token'
	assert accounts[1].cookies is None


def test_aiaiai_slot_appends_account_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_40',
		json.dumps(
			[
				{
					'name': 'AIAIAI-account-2',
					'provider': 'aiaiai',
					'cookies': {'web_device_id': 'test-device', 'session': 'test-session'},
					'api_user': '67890',
				}
			]
		),
	)

	accounts = load_accounts_config()

	assert _account_env_names()[-1] == 'EXTRA_ACCOUNTS_40'
	assert [account.name for account in accounts] == ['Main', 'AIAIAI-account-2']
	assert accounts[1].provider == 'aiaiai'
	assert accounts[1].cookies == {'web_device_id': 'test-device', 'session': 'test-session'}
	assert accounts[1].api_user == '67890'


def test_motomoto_slot_appends_account_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_36',
		json.dumps([{'name': 'MotoMoto', 'provider': 'motomoto', 'access_token': 'test-token', 'api_user': '5032'}]),
	)

	accounts = load_accounts_config()

	assert _account_env_names()[-1] == 'EXTRA_ACCOUNTS_36'
	assert [account.name for account in accounts] == ['Main', 'MotoMoto']
	assert accounts[1].provider == 'motomoto'
	assert accounts[1].api_user == '5032'


def test_ruachat_slot_accepts_username_password_account(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_37',
		json.dumps(
			[
				{
					'name': 'rua-chat',
					'provider': 'ruachat',
					'username': 'temporary-user',
					'password': 'temporary-password',
				}
			]
		),
	)

	accounts = load_accounts_config()

	assert _account_env_names()[-1] == 'EXTRA_ACCOUNTS_37'
	assert len(accounts) == 1
	assert accounts[0].provider == 'ruachat'
	assert accounts[0].username == 'temporary-user'
	assert accounts[0].password == 'temporary-password'


def test_second_sheapi_slot_appends_account_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	for slot, name, username, password in (
		(10, 'SheApi', 'first-test-user', 'first-test-password'),
		(38, 'SheApi-account-2', 'second-test-user', 'second-test-password'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps([{'name': name, 'provider': 'sheapi', 'username': username, 'password': password}]),
		)

	accounts = load_accounts_config()

	assert _account_env_names()[-2:] == ['EXTRA_ACCOUNTS_10', 'EXTRA_ACCOUNTS_38']
	assert [account.name for account in accounts] == ['Main', 'SheApi', 'SheApi-account-2']
	assert [account.provider for account in accounts[1:]] == ['sheapi', 'sheapi']
	assert [account.username for account in accounts[1:]] == ['first-test-user', 'second-test-user']
	assert [account.password for account in accounts[1:]] == ['first-test-password', 'second-test-password']
	assert all(account.has_login_credentials() for account in accounts[1:])


def test_unconfigured_second_sheapi_slot_preserves_existing_account(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS_10',
		json.dumps([{'name': 'SheApi', 'provider': 'sheapi', 'username': 'test-user', 'password': 'test-password'}]),
	)
	monkeypatch.setenv('EXTRA_ACCOUNTS_38', '')

	accounts = load_accounts_config()

	assert len(accounts) == 1
	assert accounts[0].name == 'SheApi'
	assert accounts[0].provider == 'sheapi'
	assert accounts[0].username == 'test-user'


def test_third_sheapi_slot_appends_account_without_clobbering(monkeypatch):
	_clear(monkeypatch)
	monkeypatch.setenv('ANYROUTER_ACCOUNTS', BASE)
	for slot, name, username, password in (
		(10, 'SheApi', 'first-test-user', 'first-test-password'),
		(38, 'SheApi-account-2', 'second-test-user', 'second-test-password'),
		(39, 'SheApi-thy1119', 'thy1119', 'third-test-password'),
	):
		monkeypatch.setenv(
			f'EXTRA_ACCOUNTS_{slot}',
			json.dumps([{'name': name, 'provider': 'sheapi', 'username': username, 'password': password}]),
		)

	accounts = load_accounts_config()

	assert _account_env_names()[-3:] == ['EXTRA_ACCOUNTS_10', 'EXTRA_ACCOUNTS_38', 'EXTRA_ACCOUNTS_39']
	assert [account.name for account in accounts] == ['Main', 'SheApi', 'SheApi-account-2', 'SheApi-thy1119']
	assert [account.provider for account in accounts[1:]] == ['sheapi', 'sheapi', 'sheapi']
	assert [account.username for account in accounts[1:]] == ['first-test-user', 'second-test-user', 'thy1119']
	assert [account.password for account in accounts[1:]] == [
		'first-test-password',
		'second-test-password',
		'third-test-password',
	]
	assert all(account.has_login_credentials() for account in accounts[1:])
