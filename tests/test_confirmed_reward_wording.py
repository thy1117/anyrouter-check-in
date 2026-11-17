import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import checkin
from utils.config import AccountConfig


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['test', 'sheapi'])
@pytest.mark.parametrize('already_checked', [True, False], ids=['already', 'accepted-without-reward-receipt'])
@pytest.mark.parametrize(
	'after_quota, after_used, expected_change',
	[
		pytest.param(9.0, 5.0, '本轮余额变化 -$1.00', id='decreased-balance-with-gross-increase'),
		pytest.param(9.0, 0.0, '本轮余额变化 -$1.00', id='decreased-balance'),
		pytest.param(10.0, 0.0, '本轮余额无变化', id='unchanged-balance'),
		pytest.param(10.0, 5.0, '本轮余额无变化', id='gross-increase-from-usage-only'),
		pytest.param(11.0, 0.0, '本轮余额变化 +$1.00', id='balance-increase-without-reward-receipt'),
	],
)
async def test_snapshot_changes_do_not_claim_a_new_check_in_reward(
	monkeypatch, provider, already_checked, after_quota, after_used, expected_change
):
	account = AccountConfig(cookies=None, provider=provider, name='confirmed-account')
	payload = {'success': not already_checked, 'message': '今日已签到' if already_checked else '签到成功'}
	api_success, error = checkin.parse_check_in_result(account.get_display_name(0), 200, json.dumps(payload))
	assert api_success is True and error is None
	before = {'success': True, 'quota': 10.0, 'used_quota': 0.0, 'checked_in_today': already_checked}
	after = {
		'success': True,
		'quota': after_quota,
		'used_quota': after_used,
		'checked_in_today': True,
		'check_in_submitted': not already_checked,
	}
	if already_checked:
		# A previously earned amount is not a fresh award in this round.
		after['quota_earned'] = 5.0
	else:
		after['check_in_phase'] = 'confirmed'
	state = SimpleNamespace(contains=Mock(return_value=False), mark_success=Mock(), save=Mock(), close=Mock())
	runner = AsyncMock(return_value=(api_success, before, after))
	sender = Mock(return_value={'Telegram': True})
	monkeypatch.setenv('CHECKIN_TARGET_ACCOUNTS', '')
	monkeypatch.setenv('NOTIFY_EVERY_RUN', 'true')
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', lambda: SimpleNamespace(providers={}))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: [account])
	monkeypatch.setattr(checkin, 'create_daily_success_state', lambda day: state)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: None)
	monkeypatch.setattr(checkin, 'save_balance_hash', Mock())
	monkeypatch.setattr(checkin, 'write_run_summary', Mock())
	monkeypatch.setattr(checkin, 'format_check_in_time', lambda: '2026-11-17 17:58:00')
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	monkeypatch.setattr(checkin, 'check_in_account', runner)
	monkeypatch.setattr(checkin.notify, 'push_message', sender)

	with pytest.raises(SystemExit) as outcome:
		await checkin.main()

	assert outcome.value.code == 0
	runner.assert_awaited_once()
	state.mark_success.assert_called_once_with(account, 0)
	sender.assert_called_once()
	message = sender.call_args.args[1]
	assert '签到结果：1/1 成功' in message
	assert '签到 +$' not in message
	assert expected_change in message
	if after_used:
		assert f'消耗 ${after_used:.2f}' in message
