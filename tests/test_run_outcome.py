from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import checkin
from utils.config import AccountConfig


@pytest.mark.asyncio
@pytest.mark.parametrize('outcomes, expected', [([True, True], 0), ([True, False], 1), ([False, False], 1), ([], 1)])
async def test_run_exit_and_summary_reflect_all_accounts(monkeypatch, tmp_path, outcomes, expected):
	accounts = [AccountConfig(cookies=None, name=f'test-{i}', provider='test') for i in range(len(outcomes))]
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	before = {'success': True, 'quota': 10.0, 'used_quota': 0.0}
	after = {'success': True, 'quota': 10.25, 'used_quota': 0.0}
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(side_effect=[(ok, before, after) for ok in outcomes]))
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	monkeypatch.setattr(checkin.notify, 'push_message', Mock())
	summary = tmp_path / 'summary.md'
	monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	with pytest.raises(SystemExit) as exc:
		await checkin.main()
	assert exc.value.code == expected
	assert checkin.check_in_account.await_count == len(outcomes)
	if outcomes:
		assert f'| {len(outcomes)} | {sum(outcomes)} | {len(outcomes) - sum(outcomes)} |' in summary.read_text()
	checkin.notify.push_message.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
	'api_success, before_quota, after_quota, expected',
	[
		(True, 10, 10.25, True),
		(True, 0, 0.001, True),
		(True, -1, 0, True),
		(False, 10, 10.25, False),
		(True, None, 10.25, False),
		(True, 10, None, False),
		(True, 10, 10, False),
		(True, 10, 9, False),
		(True, False, 10, False),
		(True, 0, True, False),
		(True, float('nan'), 10, False),
		(True, 10, float('inf'), False),
		(True, 'unreadable', 10, False),
	],
)
async def test_success_requires_readable_available_balance_increase(
	monkeypatch, api_success, before_quota, after_quota, expected
):
	before = {'success': True, 'quota': before_quota, 'used_quota': 0}
	# Usage can make total credited quota increase while available balance decreases.
	after = {'success': True, 'quota': after_quota, 'used_quota': 5}
	if not api_success:
		after['check_in_error'] = 'server rejected check-in'
	state = SimpleNamespace(contains=Mock(return_value=False), mark_success=Mock(), save=Mock(), close=Mock())
	monkeypatch.delenv('CHECKIN_TARGET_ACCOUNTS', raising=False)
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [AccountConfig(cookies=None, name='test-account')])
	monkeypatch.setattr(checkin, 'create_daily_success_state', lambda day: state)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(return_value=(api_success, before, after)))
	sender = Mock()
	monkeypatch.setattr(checkin.notify, 'push_message', sender)

	with pytest.raises(SystemExit) as outcome:
		await checkin.main()
	assert outcome.value.code == (0 if expected else 1)
	assert state.mark_success.call_count == int(expected)
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert f'签到结果：{int(expected)}/1 成功' in message
	if not expected:
		assert ('未确认到账' if api_success else 'server rejected check-in') in message
	assert state.close.called


@pytest.mark.asyncio
async def test_missing_target_account_exits_without_notification(monkeypatch):
	accounts = [AccountConfig(cookies=None, name='present', provider='test')]
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin.notify, 'push_message', Mock())
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', 'missing')

	with pytest.raises(SystemExit) as exc:
		await checkin.main()

	assert exc.value.code == 1
	checkin.notify.push_message.assert_not_called()


def test_summary_io_error_does_not_replace_the_run_outcome(monkeypatch, tmp_path):
	monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path))
	checkin.write_run_summary(1, 2)
