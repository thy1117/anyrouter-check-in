import json

from utils.config import AppConfig, ProviderConfig


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is False


def test_futureppo_provider_is_builtin():
	config = AppConfig.load_from_env()

	provider = config.providers['futureppo']

	assert provider.domain == 'https://api.futureppo.top'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.waf_cookie_names == ['cf_clearance']
	assert provider.use_proxy is True


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True


def test_custom_provider_profile_persistence_defaults_to_false(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert config.providers['custom'].persist_profile is False


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(name='custom', domain='https://old.example.com', persist_profile=True)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True


def test_extra_providers_are_merged_after_main_providers(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps({'custom': {'domain': 'https://old.example.com', 'use_proxy': True}}),
	)
	monkeypatch.setenv(
		'EXTRA_PROVIDERS',
		json.dumps(
			{
				'custom': {'domain': 'https://new.example.com'},
				'futureppo': {
					'domain': 'https://api.futureppo.top',
					'sign_in_path': '/api/user/checkin',
				},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['custom'].domain == 'https://new.example.com'
	assert config.providers['custom'].use_proxy is True
	assert config.providers['futureppo'].sign_in_path == '/api/user/checkin'


def test_twinkle_sub2api_provider_is_built_in(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['twinkle']

	assert provider.api_style == 'sub2api'
	assert provider.domain == 'https://big-model.smart-agi.com'
	assert provider.login_api_path == '/api/v1/auth/login'
	assert provider.sign_in_path == '/api/v1/user/daily-checkin'
	assert provider.user_info_path == '/api/v1/user/profile'
	assert provider.use_proxy is True


def test_42w_provider_uses_browser_page_for_cloudflare(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['42w']

	assert provider.domain == 'https://api.42w.shop'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.waf_cookie_names == ['cf_clearance']
	assert provider.use_proxy is True
	assert provider.http2 is False
	assert provider.request_in_page is True


def test_kapibala_provider_uses_newapi_refresh_auth(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['kapibala']

	assert provider.domain == 'https://kapibala.asia'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.auth_refresh_path == '/api/user/auth/refresh'
	assert provider.use_proxy is False


def test_cun_provider_uses_password_login_over_proxy(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['cun']

	assert provider.domain == 'https://www.cun.ai'
	assert provider.login_api_path == '/api/user/login'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.use_proxy is True


def test_nova_provider_uses_browser_page_for_cloudflare(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['nova']

	assert provider.domain == 'https://nova.vcrauo.com'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.waf_cookie_names == ['cf_clearance']
	assert provider.use_proxy is False
	assert provider.http2 is False
	assert provider.request_in_page is True


def test_nianhua_provider_uses_password_login(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)
	monkeypatch.delenv('EXTRA_PROVIDERS', raising=False)

	config = AppConfig.load_from_env()
	provider = config.providers['nianhua']

	assert provider.domain == 'https://us-3.nianhuaapi.com'
	assert provider.login_api_path == '/api/user/login'
	assert provider.sign_in_path == '/api/user/checkin'
	assert provider.user_info_path == '/api/user/self'
	assert provider.use_proxy is False
