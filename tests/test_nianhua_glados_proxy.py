"""Nianhua 走 GLaDOS 订阅节点池：每个账号分到不同节点，浏览器经 Mihomo 代理启动。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import checkin
from utils.config import AccountConfig, AppConfig
from utils.proxy import ProxyNodeAllocator

NIANHUA_ACCOUNTS = ['Nianhua-1117', 'Nianhua-thy1118', 'Nianhua-thy1119', 'Nianhua-thy1120', 'Nianhua-thy1121']


@pytest.fixture
def nianhua_provider(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	return AppConfig.load_from_env().get_provider('nianhua')


def test_nianhua_accounts_get_distinct_glados_nodes_without_ip_probe(monkeypatch, tmp_path, nianhua_provider):
	nodes = [f'Fast-B1-{n}' for n in range(1, 8)]
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.setattr(checkin, 'get_mihomo_nodes', lambda: list(nodes))
	monkeypatch.setattr(checkin, '_PROXY_ALLOCATOR', ProxyNodeAllocator(nodes, state_file=tmp_path / 'state.json'))
	probe = Mock(side_effect=AssertionError('Nianhua must not run the SheApi exit-IP probe'))
	monkeypatch.setattr(checkin, 'probe_proxy_ip', probe)

	assigned = [
		checkin.resolve_account_proxy_node(
			AccountConfig(cookies=None, provider='nianhua', name=name), nianhua_provider, i
		)
		for i, name in enumerate(NIANHUA_ACCOUNTS)
	]

	assert all(node in nodes for node in assigned)
	assert len(set(assigned)) == len(NIANHUA_ACCOUNTS)
	probe.assert_not_called()


@pytest.mark.asyncio
async def test_nianhua_gorouter_browser_launches_through_checkin_proxy(monkeypatch, nianhua_provider):
	launches = []

	async def fake_launch(settings, **kwargs):
		launches.append(kwargs)
		raise RuntimeError('stop after launch')

	monkeypatch.setattr(checkin, 'load_browser_login_settings', lambda *args, **kwargs: SimpleNamespace())
	monkeypatch.setattr(checkin, 'launch_login_context', fake_launch)
	account = AccountConfig(cookies=None, provider='nianhua', name='Nianhua-thy1118', access_token='pat', api_user='1')

	success, _, _ = await checkin.run_gorouter_check_in_in_page(account, 'Nianhua-thy1118', nianhua_provider)

	assert success is False
	assert launches == [{'use_proxy': True}]
