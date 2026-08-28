import asyncio

from utils.browser import reset_turnstile_in_page, solve_turnstile_in_page

CHALLENGE_URL = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/flow/ov1/x'
OTHER_CHALLENGE_URL = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/flow/ov1/y'
GOOD_BOX = {'x': 28.0, 'y': 28.0, 'width': 300.0, 'height': 65.0}


class FakeElement:
	def __init__(self, box):
		self._box = box

	async def bounding_box(self):
		return self._box


class FakeFrame:
	def __init__(self, url, box=None, raises=False):
		self.url = url
		self._box = box
		self._raises = raises

	async def frame_element(self):
		if self._raises:
			raise RuntimeError('frame detached')
		return FakeElement(self._box)


class FakeMouse:
	def __init__(self):
		self.events = []

	async def move(self, x, y, steps=1):
		self.events.append(('move', round(x), round(y), steps))

	async def down(self):
		self.events.append(('down',))

	async def up(self):
		self.events.append(('up',))


class FakePage:
	"""按顺序回放 evaluate 结果，记录鼠标轨迹。

	``frames_after_render`` 模拟 widget 渲染后才出现的 challenge iframe。
	"""

	def __init__(self, poll_results, frames=(), frames_after_render=None, render_error=None):
		self.frames = list(frames)
		self.mouse = FakeMouse()
		self.evaluated = []
		self.waits = []
		self._poll_results = list(poll_results)
		self._frames_after_render = frames_after_render
		self._render_error = render_error

	async def evaluate(self, script, arg=None):
		self.evaluated.append(script)
		if 'window.turnstile.render' in script:
			if self._render_error:
				raise RuntimeError(self._render_error)
			if self._frames_after_render is not None:
				self.frames = list(self._frames_after_render)
			return 'widget-0'
		if 'turnstile.remove' in script:
			return None
		return self._poll_results.pop(0) if self._poll_results else {'token': '', 'error': ''}

	async def wait_for_timeout(self, ms):
		self.waits.append(ms)


def test_returns_token_without_clicking_when_callback_fires_early():
	page = FakePage([{'token': 'tok-managed', 'error': ''}])

	assert asyncio.run(solve_turnstile_in_page(page, '0xKEY')) == ('tok-managed', None)
	assert page.mouse.events == []


def test_clicks_checkbox_then_returns_token():
	# widget 在 closed shadow root 里，只能靠 frame 的 bounding box 定位勾选框。
	page = FakePage(
		[{'token': '', 'error': ''}, {'token': 'tok-clicked', 'error': ''}],
		frames_after_render=[FakeFrame(CHALLENGE_URL, GOOD_BOX)],
	)

	assert asyncio.run(solve_turnstile_in_page(page, '0xKEY')) == ('tok-clicked', None)
	assert page.mouse.events == [
		('move', -100, -20, 1),
		('move', 50, 60, 14),
		('down',),
		('up',),
	]


def test_ignores_frames_that_existed_before_render():
	# 站点自己的 widget 不能当成我们注入的那个，否则会点错目标。
	stale = FakeFrame(CHALLENGE_URL, GOOD_BOX)
	page = FakePage([{'token': '', 'error': ''}], frames=[stale], frames_after_render=[stale])

	token, error = asyncio.run(solve_turnstile_in_page(page, '0xKEY', timeout_ms=1))

	assert token is None
	assert 'did not become interactive' in error
	assert page.mouse.events == []


def test_ignores_one_by_one_stub_frame():
	# shadow root 外只有一个 1x1 占位 iframe，不能拿它当点击目标。
	page = FakePage(
		[{'token': '', 'error': ''}],
		frames_after_render=[FakeFrame(CHALLENGE_URL, {'x': 0.0, 'y': 0.0, 'width': 1.0, 'height': 1.0})],
	)

	token, error = asyncio.run(solve_turnstile_in_page(page, '0xKEY', timeout_ms=1))

	assert token is None
	assert 'did not become interactive' in error
	assert page.mouse.events == []


def test_picks_new_frame_when_site_widget_already_present():
	stale = FakeFrame(CHALLENGE_URL, {'x': 400.0, 'y': 500.0, 'width': 300.0, 'height': 65.0})
	fresh = FakeFrame(OTHER_CHALLENGE_URL, GOOD_BOX)
	page = FakePage(
		[{'token': '', 'error': ''}, {'token': 'tok-fresh', 'error': ''}],
		frames=[stale],
		frames_after_render=[stale, fresh],
	)

	assert asyncio.run(solve_turnstile_in_page(page, '0xKEY')) == ('tok-fresh', None)
	assert ('move', 50, 60, 14) in page.mouse.events


def test_surfaces_error_callback():
	page = FakePage([{'token': '', 'error': '300030'}])

	token, error = asyncio.run(solve_turnstile_in_page(page, '0xKEY'))

	assert token is None
	assert '300030' in error


def test_rejects_empty_site_key():
	page = FakePage([])

	assert asyncio.run(solve_turnstile_in_page(page, '')) == (None, 'Turnstile site key is empty')
	assert page.evaluated == []


def test_reports_render_failure():
	page = FakePage([], render_error='api.js failed')

	token, error = asyncio.run(solve_turnstile_in_page(page, '0xKEY'))

	assert token is None
	assert 'api.js failed' in error


def test_tolerates_detached_frame_while_polling():
	page = FakePage([{'token': '', 'error': ''}], frames_after_render=[FakeFrame(CHALLENGE_URL, raises=True)])

	token, error = asyncio.run(solve_turnstile_in_page(page, '0xKEY', timeout_ms=1))

	assert token is None
	assert 'did not become interactive' in error


def test_reset_swallows_evaluate_failure():
	class BrokenPage(FakePage):
		async def evaluate(self, script, arg=None):
			raise RuntimeError('page closed')

	asyncio.run(reset_turnstile_in_page(BrokenPage([])))
