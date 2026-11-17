import hashlib
from unittest.mock import Mock

import httpx
import pytest

from utils.notify import NotificationKit, split_telegram_message


def kit(monkeypatch):
	monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-telegram-token')
	monkeypatch.setenv('TELEGRAM_CHAT_ID', '123456')
	monkeypatch.setattr('utils.notify.time.sleep', Mock())
	return NotificationKit()


def test_chinese_chunks_fit_utf8_bytes_without_truncation():
	message = '✅ 账号 thy1117｜余额 $10.25\n' * 500
	parts = split_telegram_message(message)
	assert ''.join(parts) == message
	assert all(len(part.encode('utf-8')) <= 4096 for part in parts)
	assert all(len(part.encode('utf-16-le')) // 2 <= 4096 for part in parts)


def test_numbered_parts_include_byte_budget_and_exact_receipts(monkeypatch):
	notifier = kit(monkeypatch)
	requests = []

	def post(service, url, data):
		requests.append(data)
		return httpx.Response(200, json={'ok': True, 'result': {'message_id': 100 + len(requests)}})

	monkeypatch.setattr(notifier, '_post_json', post)
	content = '😀 中文账号 <thy1117> & 余额\n' * 600
	receipts = notifier.send_telegram('全量签到', content)
	assert len(requests) > 1
	assert len(receipts) == len(requests)
	reconstructed = []
	for index, (data, receipt) in enumerate(zip(requests, receipts, strict=True), 1):
		prefix = f'[{index}/{len(requests)}]\n'
		assert data['text'].startswith(prefix)
		assert len(data['text'].encode('utf-8')) <= 4096
		assert len(data['text'].encode('utf-16-le')) // 2 <= 4096
		assert receipt['message_id'] == 100 + index
		assert receipt['utf8_bytes'] == len(data['text'].encode('utf-8'))
		assert receipt['utf16_units'] == len(data['text'].encode('utf-16-le')) // 2
		assert receipt['sha256'] == hashlib.sha256(data['text'].encode('utf-8')).hexdigest()
		assert 'parse_mode' not in data
		reconstructed.append(data['text'][len(prefix) :])
	assert ''.join(reconstructed) == f'全量签到\n\n{content}'


def test_multipart_timeout_never_replays_confirmed_part(monkeypatch, capsys):
	notifier = kit(monkeypatch)
	post = Mock(
		side_effect=[
			httpx.Response(200, json={'ok': True, 'result': {'message_id': 101}}),
			httpx.ReadTimeout('ambiguous send'),
		]
	)
	monkeypatch.setattr(notifier, '_post_json', post)
	with pytest.raises(httpx.ReadTimeout):
		notifier.send_telegram('全量签到', '中' * 5000)
	assert post.call_count == 2
	output = capsys.readouterr().out
	assert 'message_id: 101' in output
	assert 'part 2/' not in output


@pytest.mark.parametrize('message', ['', 'a' * 4096, 'a' * 4097, '中' * 2000, '😀' * 2000, '\n' * 5000])
def test_dual_budget_boundary_preserves_exact_text(message):
	parts = split_telegram_message(message)
	assert ''.join(parts) == message
	assert all(0 < len(part.encode('utf-8')) <= 4096 for part in parts)
	assert all(len(part.encode('utf-16-le')) // 2 <= 4096 for part in parts)
	if not message:
		assert parts == []


@pytest.mark.parametrize('invalid_id', [True, False, 0, -1, '101'])
def test_telegram_rejects_invalid_message_receipt(monkeypatch, invalid_id):
	notifier = kit(monkeypatch)
	response = httpx.Response(200, json={'ok': True, 'result': {'message_id': invalid_id}})
	post = Mock(return_value=response)
	monkeypatch.setattr(notifier, '_post_json', post)
	with pytest.raises(RuntimeError, match='delivery not confirmed'):
		notifier.send_telegram('全量签到', '边界测试')
	post.assert_called_once()


@pytest.mark.parametrize('minimum_parts', [10, 100])
def test_wide_part_numbers_stay_within_both_budgets(monkeypatch, minimum_parts):
	notifier = kit(monkeypatch)
	requests = []

	def post(service, url, data):
		requests.append(data)
		return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(requests)}})

	monkeypatch.setattr(notifier, '_post_json', post)
	content = 'x' * (4032 * minimum_parts)
	receipts = notifier.send_telegram('全量签到', content)
	assert len(receipts) >= minimum_parts
	assert ''.join(data['text'].split('\n', 1)[1] for data in requests) == f'全量签到\n\n{content}'
	assert all(len(data['text'].encode('utf-8')) <= 4096 for data in requests)
	assert all(len(data['text'].encode('utf-16-le')) // 2 <= 4096 for data in requests)
	assert requests[-1]['text'].startswith(f'[{len(receipts)}/{len(receipts)}]\n')
