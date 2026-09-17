import json
from unittest.mock import MagicMock, patch

from utils.config import AccountConfig, ProviderConfig
from utils.proxy import (
	active_proxy_node,
	get_current_mihomo_node,
	switch_mihomo_node,
)


def test_account_config_parses_proxy_node():
	acc1 = AccountConfig.from_dict({'name': 'A1', 'proxy_node': '家宽', 'password': 'p', 'username': 'u'}, 0)
	assert acc1.proxy_node == '家宽'

	acc2 = AccountConfig.from_dict({'name': 'A2', 'proxyNode': 'oracle-sg', 'password': 'p', 'username': 'u'}, 1)
	assert acc2.proxy_node == 'oracle-sg'

	acc3 = AccountConfig.from_dict({'name': 'A3', 'proxy': '家宽', 'password': 'p', 'username': 'u'}, 2)
	assert acc3.proxy_node == '家宽'

	# URL 类型的 proxy 不作为 proxy_node 节点名
	acc4 = AccountConfig.from_dict(
		{'name': 'A4', 'proxy': 'http://127.0.0.1:7890', 'password': 'p', 'username': 'u'}, 3
	)
	assert acc4.proxy_node is None


def test_provider_config_parses_proxy_node():
	p = ProviderConfig.from_dict('custom', {'domain': 'https://example.com', 'proxy_node': '家宽'})
	assert p.proxy_node == '家宽'


def test_get_current_mihomo_node_success():
	fake_response = MagicMock()
	fake_response.status = 200
	fake_response.read.return_value = json.dumps({'name': 'CHECKIN', 'now': 'oracle-sg'}).encode('utf-8')
	fake_response.__enter__.return_value = fake_response

	with patch('urllib.request.urlopen', return_value=fake_response):
		current = get_current_mihomo_node(controller_port=9090)
		assert current == 'oracle-sg'


def test_get_current_mihomo_node_failure():
	with patch('urllib.request.urlopen', side_effect=Exception('Connection refused')):
		current = get_current_mihomo_node(controller_port=9090)
		assert current is None


def test_switch_mihomo_node_success():
	fake_response = MagicMock()
	fake_response.status = 204
	fake_response.__enter__.return_value = fake_response

	with patch('urllib.request.urlopen', return_value=fake_response) as mock_urlopen:
		success = switch_mihomo_node('家宽', controller_port=9090)
		assert success is True
		req = mock_urlopen.call_args[0][0]
		assert req.method == 'PUT'
		assert b'"name": "\\u5bb6\\u5bbd"' in req.data or b'"name": "\xe5\xae\xb6\xe5\xae\xbd"' in req.data


def test_active_proxy_node_switches_and_restores():
	calls = []

	def fake_switch(node_name, **kwargs):
		calls.append(node_name)
		return True

	with (
		patch('utils.proxy.get_current_mihomo_node', return_value='oracle-sg'),
		patch('utils.proxy.switch_mihomo_node', side_effect=fake_switch),
	):
		with active_proxy_node('家宽', account_name='SheApi-5496'):
			assert calls == ['家宽']
		assert calls == ['家宽', 'oracle-sg']


def test_active_proxy_node_noop_when_node_empty():
	with patch('utils.proxy.switch_mihomo_node') as mock_switch:
		with active_proxy_node(None):
			pass
		mock_switch.assert_not_called()


def test_active_proxy_node_handles_switch_failure_gracefully():
	with (
		patch('utils.proxy.get_current_mihomo_node', return_value=None),
		patch('utils.proxy.switch_mihomo_node', return_value=False),
	):
		executed = False
		with active_proxy_node('家宽'):
			executed = True
		assert executed is True
