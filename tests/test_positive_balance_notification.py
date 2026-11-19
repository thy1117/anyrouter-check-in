import pytest

from checkin import format_check_in_notification


@pytest.mark.parametrize(
	'change, expected',
	[
		(0.0, '✅ Demo｜余额 $10.00'),
		(0.25, '✅ Demo｜余额 $10.00｜+$0.25'),
		(-1.0, '✅ Demo｜余额 $10.00'),
	],
)
def test_positive_balance_change_only(change, expected):
	detail = {
		'name': 'Demo',
		'after_quota': 10.0,
		'usage_increase': 0.0,
		'balance_change': change,
		'check_in_reward': change,
		'success': True,
	}
	assert format_check_in_notification(detail) == expected


def test_failed_account_keeps_failure_text():
	detail = {
		'name': 'Demo',
		'after_quota': 10.0,
		'usage_increase': 0.0,
		'balance_change': 0.0,
		'check_in_reward': 0.0,
		'success': False,
	}
	assert format_check_in_notification(detail) == '❌ Demo｜余额 $10.00｜签到失败'
