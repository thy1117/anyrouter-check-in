from checkin import format_check_in_notification, get_user_info
from utils.config import AppConfig


def test_guyscode_provider_config():
	cfg = AppConfig.load_from_env().get_provider('guyscode')
	assert cfg is not None
	assert cfg.name == 'guyscode'
	assert cfg.domain == 'https://www.guyscode.com'
	assert cfg.sign_in_path == '/api/v1/check-in'
	assert cfg.user_info_path == '/api/v1/user/profile'
	assert cfg.auth_refresh_path == '/api/v1/auth/refresh'


def test_guyscode_user_info_parsing():
	class DummyClient:
		def get(self, url, headers=None, timeout=None):
			class DummyResponse:
				status_code = 200

				def json(self):
					return {
						'code': 0,
						'message': 'success',
						'data': {
							'id': 2802,
							'email': '523728974@qq.com',
							'balance': 2.1,
							'frozen_balance': 0.0,
						},
					}

			return DummyResponse()

	info = get_user_info(DummyClient(), {}, 'https://www.guyscode.com/api/v1/user/profile')
	assert info['success'] is True
	assert info['quota'] == 2.1
	assert info['used_quota'] == 0.0


def test_guyscode_notification_format():
	detail = {
		'name': 'GuysCode-5237',
		'after_quota': 2.1,
		'check_in_reward': 0.1,
		'usage_increase': 0.0,
		'success': True,
	}
	line = format_check_in_notification(detail)
	assert line == '✅ GuysCode-5237｜余额 $2.10｜签到 +$0.10'
