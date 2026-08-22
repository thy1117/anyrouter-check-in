#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Literal


@dataclass
class ProviderConfig:
	"""Provider 配置"""

	name: str
	domain: str
	login_path: str = '/login'
	sign_in_path: str | None = '/api/user/sign_in'
	user_info_path: str = '/api/user/self'
	api_user_key: str = 'new-api-user'
	bypass_method: Literal['waf_cookies'] | None = None
	waf_cookie_names: List[str] | None = None
	use_proxy: bool = False
	persist_profile: bool = False

	def __post_init__(self):
		required_waf_cookies = set()
		if self.waf_cookie_names and isinstance(self.waf_cookie_names, List):
			for item in self.waf_cookie_names:
				name = '' if not item or not isinstance(item, str) else item.strip()
				if not name:
					print(f'[WARNING] Found invalid WAF cookie name: {item}')
					continue

				required_waf_cookies.add(name)

		if not required_waf_cookies:
			self.bypass_method = None

		self.waf_cookie_names = list(required_waf_cookies)

	@classmethod
	def from_dict(cls, name: str, data: dict, *, defaults: 'ProviderConfig | None' = None) -> 'ProviderConfig':
		"""从字典创建 ProviderConfig

		配置格式:
		- 基础: {"domain": "https://example.com"}
		- 完整: {"domain": "https://example.com", "login_path": "/login", "use_proxy": true, ...}
		"""
		default_use_proxy = defaults.use_proxy if defaults else False
		default_persist_profile = defaults.persist_profile if defaults else False
		return cls(
			name=name,
			domain=data['domain'],
			login_path=data.get('login_path', defaults.login_path if defaults else '/login'),
			sign_in_path=data.get('sign_in_path', defaults.sign_in_path if defaults else '/api/user/sign_in'),
			user_info_path=data.get('user_info_path', defaults.user_info_path if defaults else '/api/user/self'),
			api_user_key=data.get('api_user_key', defaults.api_user_key if defaults else 'new-api-user'),
			bypass_method=data.get('bypass_method', defaults.bypass_method if defaults else None),
			waf_cookie_names=data.get('waf_cookie_names', defaults.waf_cookie_names if defaults else None),
			use_proxy=data.get('use_proxy', default_use_proxy),
			persist_profile=data.get('persist_profile', default_persist_profile),
		)

	def needs_waf_cookies(self) -> bool:
		"""判断是否需要获取 WAF cookies"""
		return self.bypass_method == 'waf_cookies'

	def needs_manual_check_in(self) -> bool:
		"""判断是否需要手动调用签到接口"""
		return self.sign_in_path is not None


@dataclass
class AppConfig:
	"""应用配置"""

	providers: Dict[str, ProviderConfig]

	@classmethod
	def load_from_env(cls) -> 'AppConfig':
		"""从环境变量加载配置"""
		providers = {
			'anyrouter': ProviderConfig(
				name='anyrouter',
				domain='https://anyrouter.top',
				login_path='/login',
				sign_in_path='/api/user/sign_in',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
				use_proxy=False,
				persist_profile=True,
			),
			'agentrouter': ProviderConfig(
				name='agentrouter',
				domain='https://agentrouter.org',
				login_path='/login',
				sign_in_path=None,  # 无需签到接口，查询用户信息时自动完成签到
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc'],
				use_proxy=True,
				persist_profile=False,
			),
			'futureppo': ProviderConfig(
				name='futureppo',
				domain='https://api.futureppo.top',
				login_path='/login',
				sign_in_path='/api/user/checkin',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['cf_clearance'],
				use_proxy=True,
				persist_profile=False,
			),
		}

		# 依次加载主配置和追加配置。EXTRA_PROVIDERS 用于在无法读取原 Secret 时安全追加站点。
		for env_name in ('PROVIDERS', 'EXTRA_PROVIDERS'):
			providers_str = os.getenv(env_name)
			if not providers_str:
				continue

			try:
				providers_data = json.loads(providers_str)

				if not isinstance(providers_data, dict):
					print(f'[WARNING] {env_name} must be a JSON object, ignoring it')
					continue

				# 后加载的配置会覆盖同名 provider，并继承未显式填写的已有设置。
				loaded_count = 0
				for name, provider_data in providers_data.items():
					try:
						providers[name] = ProviderConfig.from_dict(
							name,
							provider_data,
							defaults=providers.get(name),
						)
						loaded_count += 1
					except Exception as e:
						print(f'[WARNING] Failed to parse provider "{name}" from {env_name}: {e}, skipping')

				print(f'[INFO] Loaded {loaded_count} custom provider(s) from {env_name} environment variable')
			except json.JSONDecodeError as e:
				print(f'[WARNING] Failed to parse {env_name} environment variable: {e}, ignoring it')
			except Exception as e:
				print(f'[WARNING] Error loading {env_name}: {e}, ignoring it')

		return cls(providers=providers)

	def get_provider(self, name: str) -> ProviderConfig | None:
		"""获取指定 provider 配置"""
		return self.providers.get(name)


@dataclass
class AccountConfig:
	"""账号配置"""

	cookies: dict | str | None
	api_user: str | None = None
	access_token: str | None = None
	provider: str = 'anyrouter'
	name: str | None = None
	email: str | None = None
	password: str | None = None

	@classmethod
	def from_dict(cls, data: dict, index: int) -> 'AccountConfig':
		"""从字典创建 AccountConfig"""
		provider = data.get('provider', 'anyrouter')
		name = data.get('name', f'Account {index + 1}')

		return cls(
			cookies=data.get('cookies'),
			api_user=data.get('api_user'),
			access_token=data.get('access_token') or data.get('accessToken') or data.get('token'),
			provider=provider,
			name=name if name else None,
			email=data.get('email'),
			password=data.get('password'),
		)

	def has_login_credentials(self) -> bool:
		"""是否配置了邮箱密码登录"""
		return bool(self.email and self.password)

	def has_access_token(self) -> bool:
		"""是否配置了新版 Bearer access token"""
		return bool(self.access_token)

	def get_display_name(self, index: int) -> str:
		"""获取显示名称"""
		return self.name if self.name else f'Account {index + 1}'


def load_accounts_config() -> list[AccountConfig] | None:
	"""从环境变量加载账号配置"""
	account_sources = []
	for env_name in ('ANYROUTER_ACCOUNTS', 'EXTRA_ACCOUNTS'):
		accounts_str = os.getenv(env_name)
		if not accounts_str:
			continue

		try:
			accounts_data = json.loads(accounts_str)
		except json.JSONDecodeError as e:
			print(f'ERROR: {env_name} JSON 解析失败: {e}')
			print('HINT: 常见原因 - 末尾多余逗号、使用了单引号、包含注释、或换行格式问题')
			return None

		if not isinstance(accounts_data, list):
			print(f'ERROR: {env_name} must use array format [{{}}]')
			return None

		account_sources.extend(accounts_data)
		print(f'[INFO] Loaded {len(accounts_data)} account(s) from {env_name}')

	if not account_sources:
		print('ERROR: ANYROUTER_ACCOUNTS or EXTRA_ACCOUNTS environment variable not found')
		return None

	try:
		accounts = []
		for i, account_dict in enumerate(account_sources):
			if not isinstance(account_dict, dict):
				print(f'ERROR: Account {i + 1} configuration format is incorrect')
				return None

			has_access_token = bool(
				account_dict.get('access_token') or account_dict.get('accessToken') or account_dict.get('token')
			)

			if 'api_user' not in account_dict:
				has_login = account_dict.get('email') and account_dict.get('password')
				if not has_login and not has_access_token:
					print(
						f'ERROR: Account {i + 1} missing required field (api_user) - only email+password login can omit it'
					)
					return None

			has_cookies = 'cookies' in account_dict and account_dict['cookies']
			has_login = account_dict.get('email') and account_dict.get('password')

			if not has_cookies and not has_login and not has_access_token:
				print(f'ERROR: Account {i + 1} must have cookies, access_token, or email+password')
				return None

			if 'name' in account_dict and not account_dict['name']:
				print(f'ERROR: Account {i + 1} name field cannot be empty')
				return None

			accounts.append(AccountConfig.from_dict(account_dict, i))

		return accounts
	except Exception as e:
		print(f'ERROR: Account configuration format is incorrect: {e}')
		return None
