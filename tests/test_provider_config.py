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
