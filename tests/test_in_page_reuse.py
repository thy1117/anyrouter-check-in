"""复用登录页面跑页内请求的行为测试。

清酒之前的失败是 run_check_in_in_page 又开了一个浏览器、重新 goto 登录页，
在 30s 默认超时上挂掉。这里锁住两件事：传了 page 就不再开浏览器 / 不再 goto，
以及没传 page 时旧路径仍然可用且 goto 带上了显式超时。
"""

import json
from types import SimpleNamespace

from checkin import check_in_account, run_check_in_in_page
from utils.browser import BrowserLoginResult
from utils.config import AccountConfig, AppConfig, ProviderConfig

PROVIDER = ProviderConfig(
	name='qingjiu',
	domain='https://e.com',
	sign_in_path='/api/user/checkin',
	user_info_path='/api/user/self',
	request_in_page=True,
)
ACCOUNT = AccountConfig(cookies=None, api_user='1', provider='qingjiu', name='清酒')


class FakePage:
	"""只实现 request_in_page 需要的 evaluate，并记录是否被 goto 过。"""

	def __init__(self):
		self.paths: list[tuple[str, str]] = []
		self.goto_calls: list[dict] = []
		self.added_cookies: list[dict] = []
		self.context = SimpleNamespace(add_cookies=self._add_cookies)

	async def _add_cookies(self, cookies):
		self.added_cookies.extend(cookies)

	async def goto(self, url, **kwargs):
		self.goto_calls.append({'url': url, **kwargs})

	async def evaluate(self, script, arg):
		path = arg['path']
		self.paths.append((arg['method'], path))
		if path == '/api/user/self':
			body = json.dumps({'success': True, 'data': {'quota': 500000, 'used_quota': 250000}})
		else:
			body = json.dumps({'success': True})
		return {'status': 200, 'body': body}


class FakeBrowser:
	def __init__(self, page):
		self.page = page
		self.closed = False

	async def new_page(self):
		return self.page

	async def close(self):
		self.closed = True


def _stub_browser_helpers(monkeypatch):
	async def noop(*args, **kwargs):
		return None

	monkeypatch.setattr('checkin.prepare_browser_page', noop)
	monkeypatch.setattr('checkin.wait_for_waf_ready', noop)
	monkeypatch.setattr('checkin.wait_for_cookies', noop)


def _forbid_launch(monkeypatch):
	async def boom(**kwargs):
		raise AssertionError('launch_async must not be called when a page is reused')

	monkeypatch.setattr('checkin.launch_async', boom)


async def test_reused_page_skips_browser_launch(monkeypatch):
	_forbid_launch(monkeypatch)
	page = FakePage()

	success, before, after = await run_check_in_in_page(ACCOUNT, '清酒', PROVIDER, page=page)

	assert success is True
	assert before['quota'] == 1.0
	assert after['quota'] == 1.0
	# 复用已登录页面：不再 goto 登录页，避免默认 30s 超时。
	assert page.goto_calls == []
	assert page.paths == [
		('GET', '/api/user/self'),
		('POST', '/api/user/checkin'),
		('GET', '/api/user/self'),
	]


async def test_without_page_launches_browser_and_sets_goto_timeout(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	monkeypatch.setenv('CHECKIN_WAIT_TIMEOUT_MS', '75000')
	_stub_browser_helpers(monkeypatch)
	page = FakePage()
	browser = FakeBrowser(page)

	async def fake_launch_async(**kwargs):
		return browser

	monkeypatch.setattr('checkin.launch_async', fake_launch_async)

	success, _, _ = await run_check_in_in_page(ACCOUNT, '清酒', PROVIDER)

	assert success is True
	assert len(page.goto_calls) == 1
	assert page.goto_calls[0]['url'] == 'https://e.com/login'
	assert page.goto_calls[0]['timeout'] == 75000
	assert browser.closed is True


async def test_reused_page_is_not_closed_by_callee(monkeypatch):
	_forbid_launch(monkeypatch)
	closed: list[str] = []
	page = FakePage()
	page.close = lambda: closed.append('page')

	await run_check_in_in_page(ACCOUNT, '清酒', PROVIDER, page=page)

	assert closed == []


async def test_check_in_account_hands_login_page_over_and_closes_context(monkeypatch):
	page = FakePage()
	captured: dict = {}
	state = {'closed': False}

	async def close():
		state['closed'] = True

	context = SimpleNamespace(close=close)

	async def fake_login(account_name, provider_config, provider, email, password, *, keep_open=False):
		captured['keep_open'] = keep_open
		return BrowserLoginResult(cookies={'session': 'x'}, api_user='1', context=context, page=page)

	async def fake_in_page(account, account_name, provider_config, **kwargs):
		captured['page'] = kwargs.get('page')
		return True, None, None

	monkeypatch.setattr('checkin.login_with_credentials', fake_login)
	monkeypatch.setattr('checkin.run_check_in_in_page', fake_in_page)

	account = AccountConfig(
		cookies=None,
		api_user='1',
		provider='qingjiu',
		name='清酒',
		username='u',
		password='p',
	)
	app_config = AppConfig(providers={'qingjiu': PROVIDER})

	success, _, _ = await check_in_account(account, 0, app_config)

	assert success is True
	assert captured['keep_open'] is True
	assert captured['page'] is page
	# 借出的上下文必须由 check_in_account 释放，否则浏览器进程留着不退。
	assert state['closed'] is True
