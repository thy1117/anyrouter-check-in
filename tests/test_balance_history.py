import base64
import json

import httpx
import pytest

from utils.balance_history import BalanceHistory, BalanceHistoryError
from utils.config import AccountConfig, ProviderConfig


@pytest.mark.parametrize('outcome', ['success', 'timeout_committed', 'conflict', 'not_saved'])
def test_remote_observation_uses_main_and_confirms_write(tmp_path, outcome):
	stored = None
	calls = []

	def handler(request):
		nonlocal stored
		calls.append(request)
		if request.method == 'GET':
			assert request.url.params['ref'] == 'main'
			if stored is None:
				return httpx.Response(404)
			return httpx.Response(
				200, json={'sha': 'sha1', 'content': base64.b64encode(json.dumps(stored).encode()).decode()}
			)
		body = json.loads(request.content)
		assert body['branch'] == 'main'
		if outcome == 'conflict':
			return httpx.Response(409)
		if outcome != 'not_saved':
			stored = json.loads(base64.b64decode(body['content']))
		if outcome == 'timeout_committed':
			raise httpx.ReadTimeout('ambiguous write')
		return httpx.Response(201)

	with httpx.Client(base_url='https://api.github.com', transport=httpx.MockTransport(handler)) as client:
		history = BalanceHistory(
			AccountConfig(cookies=None, api_user='7'),
			ProviderConfig('anyrouter', 'https://example.test'),
			directory=tmp_path,
			token='',
			repository='owner/repo',
			client=client,
		)
		assert history.load() is None
		if outcome in ('success', 'timeout_committed'):
			history.save({'quota': 11, 'used_quota': 0}, '2026-11-15 12:00:00')
			assert history.load()['quota'] == 11
		else:
			with pytest.raises(BalanceHistoryError):
				history.save({'quota': 11, 'used_quota': 0}, '2026-11-15 12:00:00')
	assert sum(request.method == 'PUT' for request in calls) == 1
