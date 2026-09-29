from checkin import resolve_account_proxy_node
from utils.config import DEFAULT_GLADOS_PROXY_NODES, AccountConfig, ProviderConfig


def test_multi_account_provider_without_explicit_nodes_gets_distinct_glados_nodes():
	provider = ProviderConfig(name='any_provider', domain='https://any.example.com')
	acc1 = AccountConfig(cookies=None, provider='any_provider', name='acc-1')
	acc2 = AccountConfig(cookies=None, provider='any_provider', name='acc-2')
	acc3 = AccountConfig(cookies=None, provider='any_provider', name='acc-3')

	node1 = resolve_account_proxy_node(acc1, provider, 0)
	node2 = resolve_account_proxy_node(acc2, provider, 1)
	node3 = resolve_account_proxy_node(acc3, provider, 2)

	assert node1 == DEFAULT_GLADOS_PROXY_NODES[0]
	assert node2 == DEFAULT_GLADOS_PROXY_NODES[1]
	assert node3 == DEFAULT_GLADOS_PROXY_NODES[2]
	assert len({node1, node2, node3}) == 3
