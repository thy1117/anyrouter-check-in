import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import attach_check_in_error, format_check_in_time, generate_balance_hash, resolve_check_in_error


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


def test_resolve_check_in_error_prefers_explicit_check_in_error():
	before = {'success': False, 'error': 'Failed to get user info: HTTP 401'}
	after = attach_check_in_error({'success': True, 'quota': 3.42}, 'Check-in failed - IP limit')

	assert resolve_check_in_error(before, after) == 'Check-in failed - IP limit'


def test_resolve_check_in_error_falls_back_to_before_profile_error():
	before = {'success': False, 'error': 'Authentication failed - invalid refresh token'}

	assert resolve_check_in_error(before, None) == 'Authentication failed - invalid refresh token'


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
