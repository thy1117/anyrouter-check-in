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
	monkeypatch.setattr(checkin, 'check_in_account', AsyncMock(side_effect=[(ok, None, None) for ok in outcomes]))
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


def test_summary_io_error_does_not_replace_the_run_outcome(monkeypatch, tmp_path):
	monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(tmp_path))
	checkin.write_run_summary(1, 2)
