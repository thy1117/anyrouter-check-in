import json
from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils.balance_history import BalanceHistory
from utils.config import AccountConfig, AppConfig


@pytest.fixture
def account_setup(monkeypatch, tmp_path):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	monkeypatch.delenv('CHECKIN_STATE_GITHUB_TOKEN', raising=False)
	monkeypatch.setenv('CHECKIN_BALANCE_HISTORY_DIR', str(tmp_path))
	account = AccountConfig(
		cookies={'session': 'private-session'}, name='AnyRouter', provider='anyrouter', api_user='7'
	)
	provider = AppConfig.load_from_env().providers['anyrouter']
	monkeypatch.setattr(
		checkin, 'solve_turnstile_in_page', AsyncMock(side_effect=AssertionError('No challenge requests'))
	)
	monkeypatch.setattr(checkin, 'prepare_cookies', AsyncMock(side_effect=AssertionError('No browser/WAF requests')))
	monkeypatch.setattr(checkin, 'launch_login_context', AsyncMock(side_effect=AssertionError('No browser login')))
	return account, provider


def install_response(monkeypatch, payload=None, *, statuses=(200,), raw=None):
	calls = []
	responses = iter(statuses)

	def handler(request):
		calls.append(request)
		assert request.method == 'GET'
		assert request.url.path == '/api/user/self'
		status = next(responses)
		return httpx.Response(status, json=payload) if raw is None else httpx.Response(status, text=raw)

	client = httpx.Client(transport=httpx.MockTransport(handler))
	monkeypatch.setattr(checkin.httpx, 'Client', lambda **kw: client)
	return calls


@pytest.mark.parametrize(
	'old,current,expected', [(10, 11, True), (10, 10, False), (10, 9, False), (0, 0.001, True), (-1, 0, True)]
)
async def test_anyrouter_only_gets_balance_and_compares_previous_run(
	monkeypatch, account_setup, old, current, expected
):
	account, provider = account_setup
	history = BalanceHistory(account, provider)
	history.save({'quota': old, 'used_quota': 2}, '2026-11-15 08:00:00')
	calls = install_response(
		monkeypatch, {'success': True, 'data': {'id': 7, 'quota': current * 500000, 'used_quota': 1500000}}
	)
	success, before, after = await checkin._run_account_checkin(account, 'AnyRouter', provider)
	assert success is expected
	assert before['quota'] == old and after['quota'] == current
	assert after['balance_only'] is True
	assert len(calls) == 1
	assert history.load()['quota'] == current
	assert 'private-session' not in history.path.read_text()


async def test_first_read_establishes_baseline_without_claiming_success(monkeypatch, account_setup):
	account, provider = account_setup
	install_response(monkeypatch, {'success': True, 'data': {'id': 7, 'quota': 500000}})
	success, before, after = await checkin._run_account_checkin(account, 'AnyRouter', provider)
	assert not success and before is None
	assert '基线' in after['check_in_error']
	assert BalanceHistory(account, provider).load()['quota'] == 1


@pytest.mark.parametrize('failure', ['html', 'missing_balance', 'wrong_identity', 'unauthorized'])
async def test_failed_or_wrong_account_read_preserves_previous_balance(monkeypatch, account_setup, failure):
	account, provider = account_setup
	history = BalanceHistory(account, provider)
	history.save({'quota': 10, 'used_quota': 0}, '2026-11-15 08:00:00')
	payload = {'success': True, 'data': {'id': 7, 'quota': 10000000}}
	if failure == 'html':
		install_response(monkeypatch, raw='<html>Verification required</html>')
	elif failure == 'missing_balance':
		install_response(monkeypatch, {'success': True, 'data': {'id': 7}})
	elif failure == 'wrong_identity':
		payload['data']['id'] = 8
		install_response(monkeypatch, payload)
	else:
		install_response(monkeypatch, {}, statuses=(401, 401))
	assert not (await checkin._run_account_checkin(account, 'AnyRouter', provider))[0]
	assert history.load()['quota'] == 10


async def test_stale_user_header_can_be_removed_for_get_only(monkeypatch, account_setup):
	account, provider = account_setup
	calls = install_response(monkeypatch, {'success': True, 'data': {'id': 7, 'quota': 500000}}, statuses=(401, 200))
	await checkin._run_account_checkin(account, 'AnyRouter', provider)
	assert provider.api_user_key in calls[0].headers
	assert provider.api_user_key not in calls[1].headers
	assert len(calls) == 2


def test_corrupt_baseline_is_not_reset_to_zero(monkeypatch, account_setup):
	account, provider = account_setup
	history = BalanceHistory(account, provider)
	history.path.write_text(json.dumps({'version': 1, 'quota': False, 'used_quota': 0, 'observed_at': 'today'}))
	calls = install_response(monkeypatch, {'success': True, 'data': {'id': 7, 'quota': 500000}})
	assert not checkin.read_anyrouter_balance(account, 'AnyRouter', provider)[0]
	assert not calls


def test_balance_key_survives_account_reordering_and_does_not_mix_users(account_setup):
	account, provider = account_setup
	first = BalanceHistory(account, provider).key
	account.name = 'renamed'
	assert BalanceHistory(account, provider).key == first
	account.api_user = '8'
	assert BalanceHistory(account, provider).key != first
