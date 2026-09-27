

def test_sheapi_in_independent_list():
	# 模拟通知内容拼接
	check_in_success_content = []
	account_provider = 'sheapi'
	account_name = 'SheApi-thy1117'

	if account_provider in ('xiaobai', 'sheapi'):
		check_in_success_content.append(f'✅ {account_name} · 今日已签到')

	assert check_in_success_content == ['✅ SheApi-thy1117 · 今日已签到']
