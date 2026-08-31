import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import (
	attach_check_in_error,
	format_check_in_time,
	generate_balance_hash,
	parse_gorouter_checkin_result,
	parse_gorouter_checkin_status,
	parse_gorouter_refresh_response,
	resolve_check_in_error,
	run_gorouter_check_in_in_page,
	should_notify_every_run,
)
from utils.config import AccountConfig, ProviderConfig


def test_balance_hash_changes_when_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 125.0, 'used': 20.0}}

	assert generate_balance_hash(before) != generate_balance_hash(after)


def test_balance_hash_changes_when_used_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 100.0, 'used': 21.0}}

	assert generate_balance_hash(before) != generate_balance_hash(after)


def test_balance_hash_is_stable_for_equivalent_balances():
	left = {
		'account_2': {'quota': 50.0, 'used': 1.0},
		'account_1': {'quota': 100.0, 'used': 20.0},
	}
	right = {
		'account_1': {'used': 20.0, 'quota': 100.0},
		'account_2': {'used': 1.0, 'quota': 50.0},
	}

	assert generate_balance_hash(left) == generate_balance_hash(right)


def test_format_check_in_time_converts_utc_to_shanghai(monkeypatch):
	monkeypatch.setenv('CHECKIN_TIMEZONE', 'Asia/Shanghai')

	assert format_check_in_time(datetime(2026, 8, 25, 1, 59, 28, tzinfo=timezone.utc)) == '2026-08-25 09:59:28'


def test_notify_every_run_is_enabled_by_default(monkeypatch):
	monkeypatch.delenv('NOTIFY_EVERY_RUN', raising=False)

	assert should_notify_every_run() is True


def test_notify_every_run_can_be_explicitly_disabled(monkeypatch):
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'false')

	assert should_notify_every_run() is False


def test_resolve_check_in_error_prefers_explicit_check_in_error():
	before = {'success': False, 'error': 'Failed to get user info: HTTP 401'}
	after = attach_check_in_error({'success': True, 'quota': 3.42}, 'Check-in failed - IP limit')

	assert resolve_check_in_error(before, after) == 'Check-in failed - IP limit'


def test_resolve_check_in_error_falls_back_to_before_profile_error():
	before = {'success': False, 'error': 'Authentication failed - invalid refresh token'}

	assert resolve_check_in_error(before, None) == 'Authentication failed - invalid refresh token'


def test_parse_gorouter_refresh_response_extracts_token_and_session_without_logging():
	body = '{"success":true,"data":{"access_token":"secret-token","session":{"sid":"sid-1"}}}'

	assert parse_gorouter_refresh_response(200, body) == ('secret-token', 'sid-1', None)


def test_parse_gorouter_checkin_status_reads_nested_checked_in_today():
	body = '{"success":true,"data":{"stats":{"checked_in_today":true}}}'

	assert parse_gorouter_checkin_status(200, body) == (True, None)


def test_parse_gorouter_checkin_status_requires_business_confirmation():
	body = '{"success":true,"data":{"stats":{"checked_in_today":false}}}'

	assert parse_gorouter_checkin_status(200, body) == (False, None)


def test_parse_gorouter_checkin_result_reads_awarded_quota():
	body = '{"success":true,"data":{"quota_awarded":250000,"checkin_date":"2026-08-26"}}'

	assert parse_gorouter_checkin_result(200, body) == (True, False, None)


def test_parse_gorouter_checkin_result_flags_turnstile_failure_for_retry():
	# Turnstile 中间件失败时也返回 HTTP 200，只有 message 能区分。
	body = '{"success":false,"message":"Turnstile 校验失败，请刷新重试！"}'

	succeeded, retry_turnstile, error = parse_gorouter_checkin_result(200, body)

	assert succeeded is False
	assert retry_turnstile is True
	assert 'Turnstile' in error


def test_parse_gorouter_checkin_result_does_not_retry_business_failure():
	body = '{"success":false,"message":"今日已签到"}'

	succeeded, retry_turnstile, error = parse_gorouter_checkin_result(200, body)

	assert succeeded is False
	assert retry_turnstile is False
	assert '今日已签到' in error


def test_parse_gorouter_checkin_result_handles_non_json_body():
	succeeded, retry_turnstile, error = parse_gorouter_checkin_result(502, '<html>bad gateway</html>')

	assert (succeeded, retry_turnstile) == (False, False)
	assert 'HTTP 502' in error


async def test_turnstile_flow_sends_api_user_header(monkeypatch):
	requests = []

	class Page:
		async def goto(self, *args, **kwargs):
			return None

	class Context:
		async def cookies(self):
			return []

		async def new_page(self):
			return Page()

		async def close(self):
			return None

	async def noop(*args, **kwargs):
		return None

	async def fake_launch_context(*args, **kwargs):
		return Context()

	async def fake_request(page, path, *, method='GET', headers=None):
		requests.append((method, path, dict(headers or {})))
		if path == '/api/user/self':
			return 200, '{"success":true,"data":{"id":4703,"quota":1000000,"used_quota":0}}'
		return 200, '{"success":true,"data":{"stats":{"checked_in_today":true}}}'

	monkeypatch.setattr(
		'checkin.load_browser_login_settings', lambda *args, **kwargs: SimpleNamespace(wait_timeout_ms=1000)
	)
	monkeypatch.setattr('checkin.launch_login_context', fake_launch_context)
	monkeypatch.setattr('checkin.prepare_browser_page', noop)
	monkeypatch.setattr('checkin.wait_for_waf_ready', noop)
	monkeypatch.setattr('checkin.request_in_page', fake_request)

	account = AccountConfig(
		cookies=None,
		provider='laomo',
		name='Laomo',
		access_token='secret-token',
		api_user='4703',
	)
	provider = ProviderConfig(
		name='laomo',
		domain='https://api.example.com',
		login_path='/console/personal',
		sign_in_path='/api/user/checkin',
		check_in_status_path='/api/user/checkin',
		user_info_path='/api/user/self',
		api_user_key='New-Api-User',
		checkin_turnstile=True,
	)

	success, _, _ = await run_gorouter_check_in_in_page(account, 'Laomo', provider)

	assert success is True
	assert requests
	assert all(headers['Authorization'] == 'Bearer secret-token' for _, _, headers in requests)
	assert all(headers['New-Api-User'] == '4703' for _, _, headers in requests)


def test_format_check_in_notification_unchanged_account_is_compact():
	from checkin import format_check_in_notification

	message = format_check_in_notification(
		{
			'name': 'Demo',
			'before_quota': 10.0,
			'before_used': 1.0,
			'after_quota': 10.0,
			'after_used': 1.0,
			'check_in_reward': 0.0,
			'usage_increase': 0.0,
			'balance_change': 0.0,
		}
	)

	assert message == '✅ Demo｜余额 $10.00｜签到无变化'
	assert '签到前' not in message


def test_format_check_in_notification_shows_reward_and_usage():
	from checkin import format_check_in_notification

	message = format_check_in_notification(
		{
			'name': 'Demo',
			'before_quota': 10.0,
			'before_used': 1.0,
			'after_quota': 12.5,
			'after_used': 1.5,
			'check_in_reward': 3.0,
			'usage_increase': 0.5,
			'balance_change': 2.5,
		}
	)

	assert message == '✅ Demo｜余额 $12.50｜签到 +$3.00｜消耗 $0.50'


def test_format_check_in_notification_marks_failed_account():
	from checkin import format_check_in_notification

	message = format_check_in_notification(
		{
			'name': 'Failed',
			'after_quota': 3.42,
			'check_in_reward': 0.0,
			'usage_increase': 0.0,
			'balance_change': 0.0,
			'success': False,
		}
	)

	assert message == '❌ Failed｜余额 $3.42｜签到失败'
