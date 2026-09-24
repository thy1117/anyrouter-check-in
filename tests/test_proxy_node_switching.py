import json
from unittest.mock import MagicMock, patch

import pytest

from checkin import resolve_account_proxy_node
from utils.config import AccountConfig, ProviderConfig
from utils.proxy import (
	ProxyNodeSwitchError,
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
	assert p.use_proxy is True

	p = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://example.com', 'use_proxy': False, 'proxy_nodes': [' 家宽 ', 'oracle-sg']},
	)
	assert p.proxy_nodes == ['家宽', 'oracle-sg']
	assert p.use_proxy is True


def test_provider_proxy_nodes_are_assigned_by_provider_account_order():
	provider = ProviderConfig(
		name='qingjiu',
		domain='https://qingjiu.example.com',
		proxy_nodes=['家宽', 'oracle-sg', 'railway-sg'],
	)
	accounts = [
		AccountConfig(cookies=None, provider='qingjiu', name='清酒-thy1117'),
		AccountConfig(cookies=None, provider='qingjiu', name='清酒-thy1118'),
		AccountConfig(cookies=None, provider='qingjiu', name='清酒-thy1119'),
	]

	assert [resolve_account_proxy_node(account, provider, index) for index, account in enumerate(accounts)] == [
		'家宽',
		'oracle-sg',
		'railway-sg',
	]


def test_provider_proxy_nodes_fail_when_accounts_exceed_dedicated_nodes():
	provider = ProviderConfig(name='qingjiu', domain='https://qingjiu.example.com', proxy_nodes=['家宽'])
	account = AccountConfig(cookies=None, provider='qingjiu', name='清酒-extra')

	with pytest.raises(ValueError, match='has no dedicated proxy node'):
		resolve_account_proxy_node(account, provider, 1)


def test_sheapi_proxy_node_assigns_oracle_sg_to_sheapi_5550():
	provider = ProviderConfig(
		name='sheapi',
		domain='https://www.sheapi.top',
		proxy_nodes=['railway-sg', '家宽', 'oracle-sg'],
	)
	acc1 = AccountConfig(cookies=None, provider='sheapi', name='SheAPI-112581647', proxy_node='railway-sg')
	acc2 = AccountConfig(cookies=None, provider='sheapi', name='SheApi-5496', proxy_node='家宽')
	acc3 = AccountConfig(cookies=None, provider='sheapi', name='SheApi-5550', proxy_node='railway-sg')

	assert resolve_account_proxy_node(acc1, provider, 0) == 'railway-sg'
	assert resolve_account_proxy_node(acc2, provider, 1) == '家宽'
	# SheApi-5550 is redirected to oracle-sg to avoid same-IP rate limiting
	assert resolve_account_proxy_node(acc3, provider, 2) == 'oracle-sg'


def test_resolve_account_proxy_node_normalizes_aliases():
	provider = ProviderConfig(name='custom', domain='https://example.com')
	acc_oracle = AccountConfig(cookies=None, provider='custom', name='A1', proxy_node='oracle')
	acc_railway = AccountConfig(cookies=None, provider='custom', name='A2', proxy_node='railway')

	assert resolve_account_proxy_node(acc_oracle, provider, 0) == 'oracle-sg'
	assert resolve_account_proxy_node(acc_railway, provider, 1) == 'railway-sg' 


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


def test_active_proxy_node_fails_closed_when_switch_fails():
	with (
		patch('utils.proxy.get_current_mihomo_node', return_value=None),
		patch('utils.proxy.switch_mihomo_node', return_value=False),
	):
		with pytest.raises(ProxyNodeSwitchError, match='Failed to switch proxy node'):
			with active_proxy_node('家宽'):
				pytest.fail('签到 must not run after a proxy-node switch failure')
