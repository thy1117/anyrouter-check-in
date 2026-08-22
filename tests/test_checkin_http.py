from checkin import get_user_info


class FakeResponse:
	def __init__(self, status_code, payload=None):
		self.status_code = status_code
		self._payload = payload or {}

	def json(self):
		return self._payload


class FakeClient:
	def __init__(self, responses):
		self.responses = iter(responses)
		self.calls = []

	def get(self, url, *, headers, timeout):
		self.calls.append({'url': url, 'headers': headers, 'timeout': timeout})
		return next(self.responses)


def test_get_user_info_retries_without_stale_api_user_after_401():
	client = FakeClient(
		[
			FakeResponse(401),
			FakeResponse(200, {'success': True, 'data': {'quota': 500000, 'used_quota': 250000}}),
		]
	)

	result = get_user_info(
		client,
		{'new-api-user': 'stale-user', 'Cookie': 'session=valid'},
		'https://anyrouter.top/api/user/self',
		api_user_key='new-api-user',
	)

	assert result == {
		'success': True,
		'quota': 1.0,
		'used_quota': 0.5,
		'display': ':money: Current balance: $1.0, Used: $0.5',
	}
	assert client.calls[0]['headers']['new-api-user'] == 'stale-user'
	assert client.calls[0]['headers']['X-Requested-With'] == 'XMLHttpRequest'
	assert 'new-api-user' not in client.calls[1]['headers']
	assert client.calls[1]['headers']['X-Requested-With'] == 'XMLHttpRequest'
