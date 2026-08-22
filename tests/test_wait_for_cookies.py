import asyncio

from utils.browser import wait_for_cookies


class FakeContext:
	def __init__(self, sequence):
		self.sequence = list(sequence)
		self.calls = 0

	async def cookies(self):
		self.calls += 1
		return self.sequence.pop(0) if self.sequence else []


class FakePage:
	def __init__(self, sequence):
		self.context = FakeContext(sequence)


def test_returns_immediately_when_cookie_present():
	page = FakePage([[{'name': 'cf_clearance', 'value': 'abc'}]])
	assert asyncio.run(wait_for_cookies(page, ['cf_clearance'])) == {'cf_clearance'}
	assert page.context.calls == 1


def test_polls_until_cookie_appears():
	page = FakePage([[], [], [{'name': 'cf_clearance', 'value': 'abc'}]])
	assert asyncio.run(wait_for_cookies(page, ['cf_clearance'], timeout_ms=5000)) == {'cf_clearance'}
	assert page.context.calls == 3


def test_ignores_empty_values():
	page = FakePage([[{'name': 'cf_clearance', 'value': ''}]])
	assert asyncio.run(wait_for_cookies(page, ['cf_clearance'], timeout_ms=600)) == set()


def test_gives_up_after_timeout():
	page = FakePage([[] for _ in range(20)])
	assert asyncio.run(wait_for_cookies(page, ['cf_clearance'], timeout_ms=600)) == set()


def test_no_required_cookies_skips_polling():
	page = FakePage([])
	assert asyncio.run(wait_for_cookies(page, [])) == set()
	assert page.context.calls == 0
