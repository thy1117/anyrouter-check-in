"""Exercise the actual production rollback entrypoint entirely offline."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import checkin
import checkin_october as rollback
from utils.config import AccountConfig, AppConfig, ProviderConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured(monkeypatch):
	# Record original values so each configure_engine call is scoped to this test.
	for name in (
		'_run_account_checkin',
		'load_accounts_config',
		'newapi_quota_pair',
		'get_user_info',
		'parse_user_info_response',
		'parse_sub2api_profile_response',
		'format_check_in_notification',
		'is_checked_in_status',
		'run_check_in_requests',
		'notify',
		'save_balance_hash',
	):
		monkeypatch.setattr(rollback.october, name, getattr(rollback.october, name))
	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	rollback.configure_engine()
	return rollback.october


def test_historical_engine_is_an_unchanged_snapshot():
	source = (ROOT / 'legacy/checkin_20261004.py').read_bytes()
	# Normalize checkout line endings; source text remains byte-identical to the
	# original Git blob when checked out with Git's LF setting.
	assert hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest() == (
		'09f5080dc99226815fb58f2e5393a4403b8e9357eabf11ea82f3b0c564794b22'
	)
	assert rollback.BASELINE_COMMIT == '852253d9befa060d2d14a941da110fa45fc22706'
	assert 'uv run checkin_october.py' in (ROOT / '.github/workflows/checkin.yml').read_text(encoding='utf-8')


@pytest.mark.parametrize('provider', ['anyrouter', 'guyscode', 'xiaobai', 'yuecheng', '42w'])
async def test_original_sites_use_the_october_dispatch(monkeypatch, configured, provider):
	config = AppConfig.load_from_env().providers[provider]
	old = AsyncMock(return_value=(True, None, None))
	new = AsyncMock(side_effect=AssertionError('Unexpected modern dispatch'))
	monkeypatch.setattr(rollback, '_october_dispatch', old)
	monkeypatch.setattr(checkin, '_run_account_checkin', new)
	account = AccountConfig(cookies=None, provider=provider, name='account')
	assert (await rollback.dispatch_account(account, 'account', config))[0]
	old.assert_awaited_once_with(account, 'account', config)
	new.assert_not_awaited()


@pytest.mark.parametrize(
	'provider', ['91token', 'lunora', 'sidrune', 'mtnapi', 'livelove', 'kapibala', 'twinkle', 'nianhua', 'ciyuan']
)
async def test_current_api_contracts_are_retained(monkeypatch, configured, provider):
	config = AppConfig.load_from_env().providers[provider]
	old = AsyncMock(side_effect=AssertionError('Obsolete site contract'))
	new = AsyncMock(return_value=(True, None, None))
	monkeypatch.setattr(rollback, '_october_dispatch', old)
	monkeypatch.setattr(checkin, '_run_account_checkin', new)
	account = AccountConfig(cookies=None, provider=provider, name='account')
	assert (await rollback.dispatch_account(account, 'account', config))[0]
	new.assert_awaited_once_with(account, 'account', config)
	old.assert_not_awaited()


async def test_anyrouter_restores_browser_preparation_and_checkin(monkeypatch, configured):
	account = AccountConfig(cookies={'session': 'private'}, provider='anyrouter', name='account', api_user='7')
	provider = AppConfig.load_from_env().providers['anyrouter']
	prepare = AsyncMock(return_value={'session': 'private', 'acw_tc': 'verified'})
	request = Mock(return_value=(True, None, None))
	monkeypatch.setattr(configured, 'prepare_cookies', prepare)
	monkeypatch.setattr(configured, 'run_check_in_requests', request)
	monkeypatch.setattr(checkin, 'verify_anyrouter_login', Mock(side_effect=AssertionError('No login-only shortcut')))
	assert (await rollback.dispatch_account(account, 'account', provider))[0]
	prepare.assert_awaited_once()
	request.assert_called_once()
	assert request.call_args.args[0]['acw_tc'] == 'verified'


@pytest.mark.parametrize('balance', [None, False, float('nan'), 'invalid'])
async def test_missing_balances_do_not_erase_success_or_become_zero(monkeypatch, configured, balance):
	info = {'success': True, 'quota': balance, 'used_quota': 0}
	monkeypatch.setattr(rollback, '_october_dispatch', AsyncMock(return_value=(True, info, info)))
	result = await rollback.dispatch_account(
		AccountConfig(cookies=None), 'account', ProviderConfig(name='anyrouter', domain='https://example.test')
	)
	assert result[0] is True
	assert result[1]['success'] is False and result[2]['success'] is False
	assert 'quota' not in result[1] and 'quota' not in result[2]


async def test_historical_main_processes_all_88_accounts_and_reports_partial_failure(monkeypatch, configured):
	accounts = [AccountConfig(cookies=None, provider='example', name=f'account-{i}') for i in range(88)]
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(configured.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(configured, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(configured, 'save_balance_hash', Mock())
	monkeypatch.setattr(configured, 'is_debug_enabled', lambda: False)
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	runner = AsyncMock(side_effect=[(True, None, None)] * 87 + [(False, None, {'check_in_error': 'HTTP 403'})])
	monkeypatch.setattr(configured, 'check_in_account', runner)
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(configured, 'notify', SimpleNamespace(push_message=sender))
	with pytest.raises(SystemExit) as outcome:
		await configured.main()
	assert outcome.value.code == 1
	assert runner.await_count == 88
	assert '签到结果：87/88 成功' in sender.call_args.args[1]
	assert 'account-87 · HTTP 403' in sender.call_args.args[1]


async def test_targeted_historical_main_filters_accounts_without_sending_or_changing_hash(monkeypatch, configured):
	accounts = [AccountConfig(cookies=None, provider='example', name=name) for name in ('keep', 'skip')]
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', 'keep')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.setattr(configured, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(configured, 'is_debug_enabled', lambda: False)
	sender = Mock(side_effect=AssertionError('Targeted runs cannot notify'))
	saver = Mock(side_effect=AssertionError('Targeted runs cannot replace full-run hash'))
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	monkeypatch.setattr(checkin, 'save_balance_hash', saver)
	runner = AsyncMock(return_value=(True, None, {'success': True, 'quota': 2, 'used_quota': 0}))
	monkeypatch.setattr(configured, 'check_in_account', runner)
	rollback.configure_engine()
	with pytest.raises(SystemExit) as outcome:
		await configured.main()
	assert outcome.value.code == 0
	assert runner.await_args is not None
	assert runner.await_count == 1 and runner.await_args.args[0].name == 'keep'
	sender.assert_not_called()
	saver.assert_not_called()


def test_october_bearer_keeps_latest_tokens_and_never_replays_ambiguous_refresh(monkeypatch, configured):
	account = AccountConfig(
		cookies=None, provider='guyscode', name='account', access_token='seed', refresh_token='seed-r'
	)
	provider = ProviderConfig(name='guyscode', domain='https://example.test', api_style='tokenrouter', use_proxy=False)
	state = Mock()
	state.load.return_value = ('latest', 'latest-r')
	monkeypatch.setattr(configured, 'create_xiaobai_token_state', lambda *args: state)
	requests = []

	def handler(request):
		requests.append(request)
		if request.method == 'GET':
			assert request.headers['Authorization'] == 'Bearer latest'
			return httpx.Response(401, json={'code': 401})
		assert json.loads(request.content) == {'refresh_token': 'latest-r'}
		state.save.assert_called_once_with('latest', 'latest-r', pending=True)
		raise httpx.ReadTimeout('ambiguous-refresh')

	client = httpx.Client(transport=httpx.MockTransport(handler))
	monkeypatch.setattr(configured.httpx, 'Client', lambda **kwargs: client)
	assert configured.run_bearer_check_in(account, 'account', provider)[0] is False
	assert [request.method for request in requests] == ['GET', 'POST']
	state.save.assert_called_once_with('latest', 'latest-r', pending=True)
	state.close.assert_called_once()


def test_ciphertext_storage_still_uses_main():
	from utils.xiaobai_token_state import STATE_BRANCH

	assert STATE_BRANCH == 'main'


def test_october_bearer_recognizes_current_daily_status_without_posting(monkeypatch, configured):
	account = AccountConfig(cookies=None, provider='guyscode', name='account', access_token='seed')
	provider = ProviderConfig(
		name='guyscode',
		domain='https://example.test',
		api_style='tokenrouter',
		use_proxy=False,
		user_info_path='/profile',
		check_in_status_path='/status',
		sign_in_path='/checkin',
	)
	monkeypatch.setattr(configured, 'create_xiaobai_token_state', lambda *args: None)
	requests = []

	def handler(request):
		requests.append((request.method, request.url.path))
		assert request.method == 'GET', 'An account already checked in must never submit again'
		if request.url.path == '/status':
			return httpx.Response(200, json={'code': 0, 'data': {'checked_in': True}})
		return httpx.Response(200, json={'code': 0, 'data': {'balance': 8.5}})

	client = httpx.Client(transport=httpx.MockTransport(handler))
	monkeypatch.setattr(configured.httpx, 'Client', lambda **kwargs: client)
	assert configured.run_bearer_check_in(account, 'account', provider)[0] is True
	assert requests == [('GET', '/profile'), ('GET', '/status'), ('GET', '/profile')]


@pytest.mark.parametrize('invalid', [None, True, 'invalid'])
def test_october_profile_rejects_invalid_direct_balance(configured, invalid):
	def handler(request):
		return httpx.Response(200, json={'success': True, 'data': {'balance': invalid}})

	with httpx.Client(transport=httpx.MockTransport(handler)) as client:
		result = configured.get_user_info(client, {}, 'https://example.test/profile')
	assert result['success'] is False
	assert 'quota' not in result


@pytest.mark.parametrize('provider', ['sheapi', 'anyrouter'])
def test_http_adapter_preserves_arguments_and_is_idempotent(monkeypatch, configured, provider):
	old = Mock(return_value=(True, None, None))
	new = Mock(return_value=(True, None, None))
	monkeypatch.setattr(rollback, '_october_requests', old)
	monkeypatch.setattr(checkin, 'run_check_in_requests', new)
	rollback.configure_engine()
	rollback.configure_engine()
	account = AccountConfig(cookies=None, name='account')
	config = ProviderConfig(name=provider, domain='https://example.test')
	arguments = dict(
		api_user_override='7', access_token_override='test-token', session_id_override='test-session', use_proxy=True
	)
	assert configured.run_check_in_requests({}, account, 'account', config, **arguments)[0] is True
	expected, unused = (new, old) if provider == 'sheapi' else (old, new)
	expected.assert_called_once_with({}, account, 'account', config, **arguments)
	unused.assert_not_called()


@pytest.mark.parametrize('engine_name', ['october', 'current'])
async def test_notification_ends_with_single_account_sites_even_with_missing_multi_balance(
	monkeypatch, configured, engine_name
):
	engine = configured if engine_name == 'october' else checkin
	accounts = [
		AccountConfig(cookies=None, provider=provider, name=name)
		for provider, name in (
			('42w', '42-Dodo'),
			('ciyuan', '词元站-1'),
			('elysiver', '烁-thy1117'),
			('laomo', '老魔公益站-thy1117'),
			('kapibala', 'Kapibala-1'),
			('livelove', 'LiveLove-1'),
			('llmpm', 'llm.pm-Dodo'),
			('ruachat', 'rua-chat-account-1'),
			('kapibala', 'Kapibala-2'),
			('superqiyu', 'SuperQiyu-554'),
			('yuecheng', '月城公益站-thy1117'),
			('ciyuan', '词元站-2'),
			('sheapi', 'SheApi-only'),
		)
	]
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(engine.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(engine, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(engine, 'save_balance_hash', Mock())
	monkeypatch.setattr(engine, 'is_debug_enabled', lambda: False)
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', '')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	info = {'success': True, 'quota': 2, 'used_quota': 0}
	results = [(account.provider != 'laomo', info, info) for account in accounts]
	results[-2] = (False, None, {'check_in_error': 'balance unavailable'})
	runner = AsyncMock(side_effect=results)
	monkeypatch.setattr(engine, 'check_in_account', runner)
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	with pytest.raises(SystemExit) as outcome:
		await engine.main()
	assert outcome.value.code == 1
	assert runner.await_count == len(accounts)
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert '❌ 老魔公益站-thy1117 ·' in message.split('💰 余额明细')[0]
	assert '词元站-2 · balance unavailable' in message
	rows = [
		line.split('｜', 1)[0]
		for line in message.split('💰 余额明细')[1].splitlines()
		if line.startswith(('✅ ', '❌ '))
	]
	assert rows[:3] == ['✅ 词元站-1', '✅ Kapibala-1', '✅ Kapibala-2']
	assert rows[3:] == [
		'✅ 42-Dodo',
		'✅ 烁-thy1117',
		'❌ 老魔公益站-thy1117',
		'✅ LiveLove-1',
		'✅ llm.pm-Dodo',
		'✅ rua-chat-account-1',
		'✅ SheApi-only',
		'✅ SuperQiyu-554',
		'✅ 月城公益站-thy1117',
	]
