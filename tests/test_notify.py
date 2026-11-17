import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

# 添加项目根目录到 PATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.notify import NotificationKit, split_telegram_message


@pytest.fixture
def notification_kit(monkeypatch):
	monkeypatch.setenv('EMAIL_USER', 'sender@example.com')
	monkeypatch.setenv('EMAIL_PASS', 'email_password')
	monkeypatch.setenv('EMAIL_TO', 'receiver@example.com')
	monkeypatch.setenv('PUSHPLUS_TOKEN', 'pushplus_token')
	monkeypatch.setenv('SERVERPUSHKEY', 'server_push_key')
	monkeypatch.setenv('DINGDING_WEBHOOK', 'https://oapi.dingtalk.com/robot/send?access_token=test_token')
	monkeypatch.setenv('FEISHU_WEBHOOK', 'https://open.feishu.cn/open-apis/bot/v2/hook/test_token')
	monkeypatch.setenv('WEIXIN_WEBHOOK', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test_token')
	monkeypatch.setenv('GOTIFY_URL', 'https://gotify.example.com/message')
	monkeypatch.setenv('GOTIFY_TOKEN', 'gotify_token')
	monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'telegram_token')
	monkeypatch.setenv('TELEGRAM_CHAT_ID', 'telegram_chat')
	monkeypatch.setenv('BARK_KEY', 'bark_key')
	return NotificationKit()


@pytest.fixture
def mock_httpx_client():
	with patch('httpx.Client') as mock_client_class:
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {'code': 200}
		mock_client = MagicMock()
		mock_client.post.return_value = mock_response
		mock_client_class.return_value.__enter__.return_value = mock_client
		yield mock_client, mock_response


def test_real_notification(notification_kit):
	"""真实接口测试，需要配置.env.local文件"""
	if os.getenv('ENABLE_REAL_TEST') != 'true':
		pytest.skip('未启用真实接口测试')

	notification_kit.push_message(
		'测试消息', f'这是一条测试消息\n发送时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
	)


@patch('smtplib.SMTP_SSL')
def test_send_email(mock_smtp, notification_kit):
	mock_server = MagicMock()
	mock_smtp.return_value.__enter__.return_value = mock_server

	notification_kit.send_email('测试标题', '测试内容')

	assert mock_server.login.called
	assert mock_server.send_message.called


def test_send_pushplus(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_pushplus('测试标题', '测试内容')

	mock_client.post.assert_called_once_with(
		'http://www.pushplus.plus/send',
		json={'token': 'pushplus_token', 'title': '测试标题', 'content': '测试内容', 'template': 'html'},
	)


def test_send_dingtalk(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_dingtalk('测试标题', '测试内容')

	expected_webhook = 'https://oapi.dingtalk.com/robot/send?access_token=test_token'
	expected_data = {'msgtype': 'text', 'text': {'content': '测试标题\n测试内容'}}

	mock_client.post.assert_called_once_with(expected_webhook, json=expected_data)


def test_send_feishu(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_feishu('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	args = mock_client.post.call_args[1]
	assert 'card' in args['json']


def test_send_wecom(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_wecom('测试标题', '测试内容')

	mock_client.post.assert_called_once_with(
		'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test_token',
		json={'msgtype': 'text', 'text': {'content': '测试标题\n测试内容'}},
	)


def test_send_gotify(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_gotify('测试标题', '测试内容')

	expected_url = 'https://gotify.example.com/message?token=gotify_token'
	expected_data = {'title': '测试标题', 'message': '测试内容', 'priority': 9}

	mock_client.post.assert_called_once_with(expected_url, json=expected_data)


def test_http_response_error(notification_kit):
	response = httpx.Response(500, text='server error')

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='PushPlus request failed: HTTP 500'):
			notification_kit.send_pushplus('测试', '测试')


def test_http_json_error(notification_kit):
	response = httpx.Response(200, json={'errcode': 40001, 'errmsg': 'invalid token'})

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='PushPlus request failed: invalid token'):
			notification_kit.send_pushplus('测试', '测试')


def test_missing_config(monkeypatch):
	monkeypatch.delenv('EMAIL_USER', raising=False)
	monkeypatch.delenv('EMAIL_PASS', raising=False)
	monkeypatch.delenv('EMAIL_TO', raising=False)
	monkeypatch.delenv('PUSHPLUS_TOKEN', raising=False)
	kit = NotificationKit()

	with pytest.raises(ValueError, match='Email configuration not set'):
		kit.send_email('测试', '测试')

	with pytest.raises(ValueError, match='PushPlus Token not configured'):
		kit.send_pushplus('测试', '测试')


def test_push_message(notification_kit, monkeypatch):
	send_methods = [
		'send_email',
		'send_pushplus',
		'send_serverPush',
		'send_dingtalk',
		'send_feishu',
		'send_wecom',
		'send_gotify',
		'send_telegram',
		'send_bark',
	]
	mocks = {}
	for method_name in send_methods:
		method_mock = MagicMock()
		mocks[method_name] = method_mock
		monkeypatch.setattr(notification_kit, method_name, method_mock)

	results = notification_kit.push_message('测试标题', '测试内容')

	for method_mock in mocks.values():
		assert method_mock.called
	assert len(results) == len(send_methods)
	assert all(results.values())


@pytest.mark.parametrize('message', ['a' * 4096, 'a' * 4368, '✅ 账号 <test> & 余额\n' * 600, '😀' * 3000])
def test_telegram_chunks_preserve_content_and_fit_limit(message):
	parts = split_telegram_message(message)
	assert ''.join(parts) == message
	assert all(0 < len(part.encode('utf-16-le')) // 2 <= 4096 for part in parts)
	if len(message.encode('utf-16-le')) // 2 <= 4096 and len(message.encode('utf-8')) <= 4096:
		assert parts == [message]


def test_telegram_chunks_prefer_line_boundaries():
	message = '账号一\n账号二\n账号三'
	assert split_telegram_message(message, limit=6) == ['账号一\n', '账号二\n', '账号三']


def test_send_telegram_long_message_is_plain_text_and_paced(mock_httpx_client, notification_kit, monkeypatch):
	client, response = mock_httpx_client
	response.json.return_value = {'ok': True, 'result': {'message_id': 123}}
	sleep = Mock()
	monkeypatch.setattr('utils.notify.time.sleep', sleep)
	content = '✅ 账号 <name> & 余额\n' * 600
	notification_kit.send_telegram('签到 <汇总>', content)
	requests = [call.kwargs['json'] for call in client.post.call_args_list]
	assert len(requests) > 1
	assert ''.join(data['text'].split('\n', 1)[1] for data in requests) == f'签到 <汇总>\n\n{content}'
	assert all(data['text'].startswith(f'[{index}/{len(requests)}]\n') for index, data in enumerate(requests, 1))
	assert all('parse_mode' not in data and data['chat_id'] == 'telegram_chat' for data in requests)
	assert all(len(data['text'].encode('utf-8')) <= 4096 for data in requests)
	assert all(len(data['text'].encode('utf-16-le')) // 2 <= 4096 for data in requests)
	assert sleep.call_count == len(requests) - 1
	sleep.assert_called_with(1.0)


def test_send_telegram_short_message_has_receipt(mock_httpx_client, notification_kit, capsys):
	client, response = mock_httpx_client
	response.json.return_value = {'ok': True, 'result': {'message_id': 915}}
	notification_kit.send_telegram('标题', '内容')
	client.post.assert_called_once_with(
		'https://api.telegram.org/bottelegram_token/sendMessage',
		json={'chat_id': 'telegram_chat', 'text': '标题\n\n内容'},
	)
	assert 'part 1/1, message_id: 915' in capsys.readouterr().out


@pytest.mark.parametrize('payload', [None, [], {'ok': True}, {'ok': True, 'result': {}}, {'code': 200}])
def test_telegram_requires_message_receipt(mock_httpx_client, notification_kit, payload):
	_, response = mock_httpx_client
	response.json.return_value = payload
	with pytest.raises(RuntimeError, match='delivery not confirmed'):
		notification_kit.send_telegram('标题', '内容')


def test_telegram_rejects_non_json_success(mock_httpx_client, notification_kit):
	_, response = mock_httpx_client
	response.json.side_effect = ValueError('invalid JSON')
	with pytest.raises(RuntimeError, match='not valid JSON'):
		notification_kit.send_telegram('标题', '内容')


def test_telegram_reports_server_error_without_credentials(mock_httpx_client, notification_kit):
	_, response = mock_httpx_client
	response.status_code = 400
	response.json.return_value = {
		'ok': False,
		'description': 'Bad Request: message is too long telegram_token telegram_chat',
	}
	with pytest.raises(RuntimeError, match='HTTP 400 - Bad Request: message is too long') as exc:
		notification_kit.send_telegram('标题', '内容')
	assert 'telegram_token' not in str(exc.value)
	assert 'telegram_chat' not in str(exc.value)


def test_push_message_records_partial_telegram_delivery(mock_httpx_client, notification_kit, monkeypatch, capsys):
	client, _ = mock_httpx_client
	client.post.side_effect = [
		httpx.Response(200, json={'ok': True, 'result': {'message_id': 1}}),
		httpx.Response(400, json={'ok': False, 'description': 'Bad Request: rejected'}),
	]
	monkeypatch.setattr('utils.notify.time.sleep', Mock())
	for method in ('email', 'pushplus', 'serverPush', 'dingtalk', 'feishu', 'wecom', 'gotify', 'bark'):
		monkeypatch.setattr(notification_kit, f'send_{method}', Mock(side_effect=ValueError('not configured')))
	results = notification_kit.push_message('标题', 'x' * 9000)
	assert results['Telegram'] is False
	assert not any(results.values())
	assert client.post.call_count == 2
	output = capsys.readouterr().out
	assert '[Telegram]: Message push failed!' in output
	assert '[Telegram]: Message push successful!' not in output


def test_push_message_returns_each_channel_result(notification_kit, monkeypatch, capsys):
	for method in ('email', 'pushplus', 'serverPush', 'dingtalk', 'feishu', 'wecom', 'gotify', 'bark'):
		monkeypatch.setattr(notification_kit, f'send_{method}', Mock())
	monkeypatch.setattr(
		notification_kit,
		'send_telegram',
		Mock(side_effect=httpx.ConnectError('Failed URL https://api.telegram.org/bottelegram_token/sendMessage')),
	)
	results = notification_kit.push_message('标题', '内容')
	assert results['Telegram'] is False
	assert results['Email'] is True
	assert 'telegram_token' not in capsys.readouterr().out
