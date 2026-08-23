import json

from checkin import _sub2api_token_from_response, parse_sub2api_profile_response, unwrap_api_data


class FakeResponse:
	def __init__(self, status_code, payload):
		self.status_code = status_code
		self._payload = payload
		self.text = json.dumps(payload)

	def json(self):
		return self._payload


def test_unwrap_sub2api_envelope():
	assert unwrap_api_data({'code': 0, 'message': 'success', 'data': {'balance': 12.6}}) == {'balance': 12.6}


def test_parse_sub2api_profile_response():
	body = json.dumps(
		{
			'code': 0,
			'message': 'success',
			'data': {'id': 1, 'username': 'Dodo', 'balance': 12.6, 'total_used': 0.25},
		}
	)

	assert parse_sub2api_profile_response(200, body) == {
		'success': True,
		'quota': 12.6,
		'used_quota': 0.25,
		'display': ':money: Current balance: $12.6000, Used: $0.2500',
	}


def test_parse_sub2api_profile_error():
	result = parse_sub2api_profile_response(401, '{"code":401}')
	assert result == {'success': False, 'error': 'Failed to get user info: HTTP 401'}


def test_extract_sub2api_tokens_from_envelope():
	response = FakeResponse(
		200,
		{
			'code': 0,
			'data': {
				'access_token': 'access',
				'refresh_token': 'refresh',
				'expires_in': 900,
			},
		},
	)

	assert _sub2api_token_from_response(response, 'Twinkle') == ('access', 'refresh')
