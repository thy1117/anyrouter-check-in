import json

from utils.config import load_accounts_config


def test_extra_accounts_are_appended_to_main_accounts(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'name': 'Main', 'cookies': {'session': 'main'}, 'api_user': '1'}]),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS',
		json.dumps(
			[
				{
					'name': 'FuturePPO',
					'provider': 'futureppo',
					'cookies': {'session': 'extra'},
					'api_user': '2',
				}
			]
		),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert [account.name for account in accounts] == ['Main', 'FuturePPO']
	assert accounts[1].provider == 'futureppo'


def test_extra_accounts_can_be_used_without_main_accounts(monkeypatch):
	monkeypatch.delenv('ANYROUTER_ACCOUNTS', raising=False)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS',
		json.dumps([{'name': 'Only extra', 'cookies': {'session': 'extra'}, 'api_user': '3'}]),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].name == 'Only extra'


def test_access_token_account_is_valid_without_cookies(monkeypatch):
	monkeypatch.delenv('ANYROUTER_ACCOUNTS', raising=False)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS',
		json.dumps([{'name': 'Token account', 'access_token': 'new-api-token'}]),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert accounts[0].access_token == 'new-api-token'
	assert accounts[0].has_access_token() is True


def test_extra_account_with_same_name_overrides_main_account(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'name': '小鸡毛-thy1117', 'cookies': {'session': 'expired'}, 'api_user': 'old'}]),
	)
	monkeypatch.setenv(
		'EXTRA_ACCOUNTS',
		json.dumps([{'name': '小鸡毛-thy1117', 'access_token': 'fresh-token'}]),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert len(accounts) == 1
	assert accounts[0].access_token == 'fresh-token'
	assert accounts[0].cookies is None
