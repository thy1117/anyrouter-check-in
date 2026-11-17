import httpx
import pytest

import checkin
from utils.config import AccountConfig, AppConfig


@pytest.mark.parametrize('status', [401, 403])
def test_sheapi_failed_authentication_never_refreshes_or_submits(monkeypatch, status):
	provider = AppConfig.load_from_env().providers['sheapi']
	account = AccountConfig(
		cookies=None, provider='sheapi', name='SheApi-thy1117', access_token='fake-retired-token', api_user='1712'
	)
	requests = []

	def handler(request):
		requests.append((request.method, request.url.path))
		assert request.method == 'GET' and request.url.path == '/api/user/self'
		return httpx.Response(status, json={'success': False, 'message': 'Legacy access tokens have been retired'})

	client = httpx.Client
	monkeypatch.setattr(
		checkin.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
	)

	def no_action(*args, **kwargs):
		pytest.fail('Rejected SheApi authentication must stop before refresh, CAPTCHA or check-in')

	monkeypatch.setattr(checkin, 'refresh_access_token', no_action)
	monkeypatch.setattr(checkin, 'execute_check_in_result', no_action)
	monkeypatch.setattr(checkin, 'execute_captcha_check_in_result', no_action)
	success, before, after = checkin.run_check_in_requests({}, account, 'SheApi-thy1117', provider)
	assert success is False
	assert before is not None and before['success'] is False
	assert after is not None and after['check_in_phase'] == 'not_submitted'
	assert after['check_in_submitted'] is False
	assert 'HTTP ' + str(status) in after['check_in_error']
	assert requests and all(method == 'GET' and path == '/api/user/self' for method, path in requests)
