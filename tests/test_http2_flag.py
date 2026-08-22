from utils.config import AppConfig, ProviderConfig


def test_futureppo_disables_http2(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	config = AppConfig.load_from_env()

	# Cloudflare 校验 h2 指纹，httpx 的 h2 握手拿着有效 cf_clearance 也会 403。
	assert config.providers['futureppo'].http2 is False


def test_http2_defaults_to_true(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)
	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].http2 is True
	assert config.providers['agentrouter'].http2 is True


def test_http2_can_be_overridden_from_dict():
	provider = ProviderConfig.from_dict('custom', {'domain': 'https://example.com', 'http2': False})
	assert provider.http2 is False


def test_http2_inherits_builtin_default(monkeypatch):
	monkeypatch.setenv('EXTRA_PROVIDERS', '{"futureppo": {"domain": "https://api.futureppo.top"}}')
	monkeypatch.delenv('PROVIDERS', raising=False)
	config = AppConfig.load_from_env()

	# 只覆盖 domain 时不应把 http2 退回默认 True，否则又会被 CF 拦。
	assert config.providers['futureppo'].http2 is False
