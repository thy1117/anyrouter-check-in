import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import generate_balance_hash


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
