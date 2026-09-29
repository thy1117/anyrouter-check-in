from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from checkin import run_bearer_check_in
from utils.config import AccountConfig, ProviderConfig


def test_guyscode_loads_and_persists_rotating_token_state(monkeypatch):
	mock_state = MagicMock()
	mock_state.load.return_value = ('persisted-access', 'persisted-refresh')

	monkeypatch.setattr('checkin.create_xiaobai_token_state', lambda acc, domain: mock_state)

	account = AccountConfig(
		cookies=None,
		name='GuysCode-5237',
		provider='guyscode',
		access_token='old-access',
		refresh_token='old-refresh',
	)
	provider = ProviderConfig(
		name='guyscode',
		domain='https://www.guyscode.com',
		api_style='tokenrouter',
		login_path='/dashboard',
		sign_in_path='/api/v1/check-in',
		check_in_status_path='/api/v1/check-in/status',
		user_info_path='/api/v1/user/profile',
		auth_refresh_path='/api/v1/auth/refresh',
		use_proxy=False,
	)

	# Mock HTTP client responses for profile and status
	fake_profile = SimpleNamespace(
		status_code=200,
		text='{"code": 0, "data": {"balance": 2.1, "used_balance": 0.0}}',
		json=lambda: {'code': 0, 'data': {'balance': 2.1, 'used_balance': 0.0}},
	)
	fake_status = SimpleNamespace(
		status_code=200,
		json=lambda: {'code': 0, 'data': {'checked_in_today': True}},
	)

	with patch('httpx.Client.get') as mock_get:
		mock_get.side_effect = [fake_profile, fake_status, fake_profile]
		success, before, after = run_bearer_check_in(account, 'GuysCode-5237', provider)

	assert success is True
	mock_state.load.assert_called_once()
