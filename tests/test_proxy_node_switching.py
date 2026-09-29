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


def test_xiaobai_env_proxy_node_overrides_dynamic_allocator(monkeypatch):
	monkeypatch.setenv('XIAOBAI_PROXY_NODE', 'oracle-sg')
	monkeypatch.setattr('checkin.get_proxy_server', lambda **kwargs: 'http://127.0.0.1:7890')
	provider = ProviderConfig(name='xiaobai', domain='https://token.dialoguedui.com', use_proxy=True)
	account = AccountConfig(cookies=None, provider='xiaobai', name='小白Code-1125')

	assert resolve_account_proxy_node(account, provider, 0) == 'oracle-sg'


def test_default_multi_account_providers_do_not_pin_nodes(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	from utils.config import AppConfig

	providers = AppConfig.load_from_env().providers
	assert providers['qingjiu'].proxy_nodes is None
	assert providers['sheapi'].proxy_nodes is None
	assert providers['xiaobai'].proxy_nodes is None


def test_fixed_proxy_node_config_is_rejected_now_that_glados_is_dynamic():
	provider = ProviderConfig(
		name='qingjiu',
		domain='https://qingjiu.example.com',
		proxy_nodes=['家宽', 'oracle-sg', 'railway-sg'],
	)
	account = AccountConfig(cookies=None, provider='qingjiu', name='清酒-thy1117')

	with pytest.raises(ValueError, match='fixed proxy node configuration'):
		resolve_account_proxy_node(account, provider, 0)


def test_account_level_fixed_proxy_node_is_rejected():
	provider = ProviderConfig(name='custom', domain='https://example.com')
	account = AccountConfig(cookies=None, provider='custom', name='A1', proxy_node='oracle')

	with pytest.raises(ValueError, match='fixed proxy node configuration'):
		resolve_account_proxy_node(account, provider, 0)


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
