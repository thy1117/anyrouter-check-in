from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import checkin
from utils.config import AccountConfig


@pytest.mark.asyncio
async def test_success_is_persisted_before_starting_the_next_account(monkeypatch):
	events = []

	class State:
		def contains(self, account, index):
			return False

		def mark_success(self, account, index):
			events.append(('confirmed', index))

		def save(self):
			events.append(('saved', None))

		def close(self):
			pass

	accounts = [AccountConfig(cookies=None, name=f'test-{i}', provider='test') for i in range(2)]

	async def run(account, index, config, **kwargs):
		events.append(('started', index))
		return True, None, None

	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.setattr(checkin, 'create_daily_success_state', lambda day: State())
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'check_in_account', run)
	monkeypatch.setattr(checkin.notify, 'push_message', Mock())
	with pytest.raises(SystemExit) as outcome:
		await checkin.main()
	assert outcome.value.code == 0
	assert events.index(('saved', None)) < events.index(('started', 1))


@pytest.mark.asyncio
async def test_later_daily_runs_only_call_accounts_without_success(monkeypatch, tmp_path):
	accounts = [AccountConfig(cookies=None, name=f'test-{i}', provider='test') for i in range(2)]
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', str(tmp_path / 'daily.json'))
	monkeypatch.delenv('CHECKIN_STATE_GITHUB_TOKEN', raising=False)
	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-12 18:00:00')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	monkeypatch.setattr(checkin.notify, 'push_message', Mock())
	runner = AsyncMock(side_effect=[(True, None, None), (False, None, None)])
	monkeypatch.setattr(checkin, 'check_in_account', runner)
	with pytest.raises(SystemExit) as first:
		await checkin.main()
	assert first.value.code == 1
	assert runner.await_count == 2

	runner.reset_mock(side_effect=True)
	runner.side_effect = [(True, None, None)]
	with pytest.raises(SystemExit) as second:
		await checkin.main()
	assert second.value.code == 0
	assert runner.await_count == 1
	assert runner.await_args is not None
	assert runner.await_args.args[:2] == (accounts[1], 1)
	assert runner.await_args.kwargs['provider_account_index'] == 1

	runner.reset_mock(side_effect=True)
	with pytest.raises(SystemExit) as third:
		await checkin.main()
	assert third.value.code == 0
	runner.assert_not_awaited()

	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-13 00:11:00')
	runner.side_effect = [(True, None, None), (True, None, None)]
	with pytest.raises(SystemExit) as tomorrow:
		await checkin.main()
	assert tomorrow.value.code == 0
	assert runner.await_count == 2


def test_daily_state_does_not_store_credentials_or_confuse_accounts(tmp_path):
	from utils.daily_success import DailySuccessState

	account = AccountConfig(cookies=None, name='same-label', provider='site', api_user='1', access_token='secret')
	state = DailySuccessState('2026-11-12', str(tmp_path / 'state.json'))
	state.mark_success(account, 0)
	assert state.contains(account, 0)
	assert not state.contains(AccountConfig(cookies=None, name='same-label', provider='other', api_user='1'), 0)
	assert not state.contains(AccountConfig(cookies=None, name='same-label', provider='site', api_user='2'), 0)
	text = (tmp_path / 'state.json').read_text()
	assert 'secret' not in text and 'same-label' not in text


@pytest.mark.parametrize('content', ['invalid json', '{}', '{"version":1,"day":"2026-11-12","confirmed":[true]}'])
def test_corrupt_daily_state_prevents_account_replay(tmp_path, content):
	from utils.daily_success import DailyStateError, DailySuccessState

	path = tmp_path / 'state.json'
	path.write_text(content)
	with pytest.raises(DailyStateError):
		DailySuccessState('2026-11-12', str(path))


def test_remote_state_reconciles_an_ambiguous_write_without_replay(tmp_path):
	import base64
	import json

	import httpx

	from utils.daily_success import DailySuccessState

	remote = None
	puts = []

	def handle(request):
		nonlocal remote
		if request.method == 'GET':
			if remote is None:
				return httpx.Response(404)
			return httpx.Response(200, json={'sha': 'state-sha', 'content': remote})
		puts.append(json.loads(request.content))
		remote = puts[-1]['content']
		raise httpx.ReadTimeout('ambiguous acknowledgement', request=request)

	client = httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(handle))
	state = DailySuccessState('2026-11-12', str(tmp_path / 'state.json'), client=client, repository='owner/repo')
	state.mark_success(AccountConfig(cookies=None, name='done', provider='site'), 0)
	state.save()
	assert len(puts) == 1
	assert remote is not None
	assert json.loads(base64.b64decode(remote))['day'] == '2026-11-12'
	assert state.dirty is False
	state.close()


def test_remote_conflict_merges_confirmed_accounts_without_losing_success(tmp_path):
	import base64
	import json

	import httpx

	from utils.daily_success import DailySuccessState

	first = AccountConfig(cookies=None, name='first', provider='site')
	second = AccountConfig(cookies=None, name='second', provider='site')
	remote = None
	puts = []

	def handle(request):
		nonlocal remote
		if request.method == 'GET':
			if remote is None:
				return httpx.Response(404)
			return httpx.Response(200, json={'sha': f'sha-{len(puts)}', 'content': remote})
		body = json.loads(request.content)
		puts.append(body)
		if len(puts) == 1:
			other = {'version': 1, 'day': '2026-11-12', 'confirmed': [DailySuccessState.account_key(second, 1)]}
			remote = base64.b64encode(json.dumps(other).encode()).decode()
			return httpx.Response(409)
		remote = body['content']
		return httpx.Response(200)

	client = httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(handle))
	state = DailySuccessState('2026-11-12', str(tmp_path / 'state.json'), client=client, repository='owner/repo')
	state.mark_success(first, 0)
	state.save()
	assert len(puts) == 2
	assert state.contains(first, 0) and state.contains(second, 1)
	assert len(json.loads(base64.b64decode(puts[-1]['content']))['confirmed']) == 2
	state.close()


def test_remote_state_failure_does_not_fall_back_to_full_replay(tmp_path):
	import httpx

	from utils.daily_success import DailyStateError, DailySuccessState

	client = httpx.Client(
		base_url='https://api.github.com',
		transport=httpx.MockTransport(lambda request: httpx.Response(503)),
	)
	with pytest.raises(DailyStateError, match='HTTP 503'):
		DailySuccessState('2026-11-12', str(tmp_path / 'state.json'), client=client, repository='owner/repo')
	client.close()


@pytest.mark.asyncio
async def test_targeted_account_can_be_rechecked_after_daily_success(monkeypatch, tmp_path):
	from utils.daily_success import DailySuccessState

	account = AccountConfig(cookies=None, name='target', provider='test')
	path = tmp_path / 'daily.json'
	state = DailySuccessState('2026-11-12', str(path))
	state.mark_success(account, 0)
	monkeypatch.setenv('CHECKIN_DAILY_STATE_FILE', str(path))
	monkeypatch.delenv('CHECKIN_STATE_GITHUB_TOKEN', raising=False)
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', 'target')
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-12 20:00:00')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	sender = Mock()
	monkeypatch.setattr(checkin.notify, 'push_message', sender)
	runner = AsyncMock(return_value=(True, None, None))
	monkeypatch.setattr(checkin, 'check_in_account', runner)
	with pytest.raises(SystemExit) as outcome:
		await checkin.main()
	assert outcome.value.code == 0
	runner.assert_awaited_once()
	sender.assert_not_called()
