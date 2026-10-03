import json
from contextlib import nullcontext
from unittest.mock import Mock

import httpx
import pytest

from utils.proxy import ProxyNodeAllocator, ProxyNodeSwitchError


def test_five_accounts_use_distinct_ips_not_just_distinct_node_names(tmp_path):
	path = tmp_path / 'state.json'
	path.write_text(json.dumps({'guyscode': {'untouched': {'node': 'original'}}}))
	ips = dict(zip('abcdefg', ['8.8.8.8', '8.8.8.8', '1.1.1.1', '1.1.1.1', '9.9.9.9', '208.67.222.222', '64.6.64.6']))
	allocator = ProxyNodeAllocator(list(ips), state_file=path)
	for number in range(5):
		allocator.assign('sheapi', str(number), ip_probe=ips.__getitem__)
	state = json.loads(path.read_text())
	assert len({entry['ip'] for entry in state['sheapi'].values()}) == 5
	assert state['guyscode'] == {'untouched': {'node': 'original'}}


def test_duplicate_or_unreachable_ips_fail_closed_and_preserve_assignments(tmp_path):
	path = tmp_path / 'state.json'
	allocator = ProxyNodeAllocator(['a', 'b', 'c'], state_file=path)
	probe = Mock(side_effect=['8.8.8.8', '8.8.8.8', ProxyNodeSwitchError('unreachable')])
	assert allocator.assign('sheapi', 'one', ip_probe=probe) == 'a'
	with pytest.raises(ProxyNodeSwitchError, match='distinct'):
		allocator.assign('sheapi', 'two', ip_probe=probe)
	assert list(json.loads(path.read_text())['sheapi']) == ['one']


def test_cached_node_is_reprobed_and_ip_collisions_are_reassigned(tmp_path):
	path = tmp_path / 'state.json'
	allocator = ProxyNodeAllocator(['a', 'b', 'c'], state_file=path)
	assert allocator.assign('sheapi', 'one', ip_probe=lambda node: '8.8.8.8') == 'a'
	assert allocator.assign('sheapi', 'two', ip_probe=lambda node: '1.1.1.1') == 'b'
	probe = Mock(side_effect=lambda node: {'a': '1.1.1.1', 'c': '9.9.9.9'}[node])
	assert allocator.assign('sheapi', 'one', ip_probe=probe) == 'c'
	assert [call.args[0] for call in probe.call_args_list] == ['a', 'c']


@pytest.mark.parametrize('failure', [None, 'trace_http', 'api_http', 'api_error', 'private', 'malformed', 'changed'])
def test_probe_uses_target_origin_and_rejects_unusable_exits(monkeypatch, failure):
	from utils import proxy

	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.setattr(proxy, 'active_proxy_node', lambda node: nullcontext())
	requests = []

	def handler(request):
		requests.append(request)
		assert request.url.host == 'www.sheapi.top'
		assert request.method == 'GET' and 'authorization' not in request.headers
		if request.url.path == '/api/status':
			return httpx.Response(503 if failure == 'api_http' else 200, json={'success': failure != 'api_error'})
		ip = {'private': '127.0.0.1', 'malformed': 'not-an-ip'}.get(failure, '8.8.8.8')
		if failure == 'changed' and len(requests) > 1:
			ip = '1.1.1.1'
		return httpx.Response(403 if failure == 'trace_http' else 200, text=f'ip={ip}\n')

	client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)

	def factory(**kwargs):
		assert kwargs['proxy'] == 'http://127.0.0.1:7890'
		assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
		return client

	monkeypatch.setattr(proxy.httpx, 'Client', factory)
	if failure:
		with pytest.raises(ProxyNodeSwitchError):
			proxy.probe_proxy_ip('node-a', 'https://www.sheapi.top')
	else:
		assert proxy.probe_proxy_ip('node-a', 'https://www.sheapi.top') == '8.8.8.8'
		assert [request.url.path for request in requests] == ['/cdn-cgi/trace', '/api/status', '/cdn-cgi/trace']


def test_sheapi_cannot_fall_back_to_unverified_default_or_direct_proxy(monkeypatch):
	from checkin import resolve_account_proxy_node
	from utils.config import AccountConfig, AppConfig

	account = AccountConfig(cookies=None, provider='sheapi', name='one')
	provider = AppConfig.load_from_env().get_provider('sheapi')
	monkeypatch.delenv('CHECKIN_PROXY_URL', raising=False)
	with pytest.raises(ProxyNodeSwitchError):
		resolve_account_proxy_node(account, provider, 0)
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.setattr('checkin.get_mihomo_nodes', lambda: [])
	with pytest.raises(ProxyNodeSwitchError):
		resolve_account_proxy_node(account, provider, 0)


def test_only_sheapi_enables_ip_verification(monkeypatch, tmp_path):
	import checkin
	from utils.config import AccountConfig, AppConfig

	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.setattr(checkin, 'get_mihomo_nodes', lambda: ['a', 'b'])
	allocator = ProxyNodeAllocator(['a', 'b'], state_file=tmp_path / 'state.json')
	monkeypatch.setattr(checkin, '_PROXY_ALLOCATOR', allocator)
	probe = Mock(return_value='8.8.8.8')
	monkeypatch.setattr(checkin, 'probe_proxy_ip', probe)
	providers = AppConfig.load_from_env()
	for provider in ('guyscode', 'sheapi'):
		account = AccountConfig(cookies=None, provider=provider, name='one')
		assert checkin.resolve_account_proxy_node(account, providers.get_provider(provider), 0) == 'a'
	probe.assert_called_once_with('a', 'https://www.sheapi.top')


@pytest.mark.asyncio
async def test_unavailable_distinct_ip_is_an_account_failure_not_a_global_crash(monkeypatch):
	import checkin
	from utils.config import AccountConfig, AppConfig

	monkeypatch.setattr(checkin, 'resolve_account_proxy_node', Mock(side_effect=ProxyNodeSwitchError('no distinct IP')))
	account = AccountConfig(cookies=None, provider='sheapi', name='one')
	result = await checkin.check_in_account(account, 0, AppConfig.load_from_env())
	assert result[0] is False
