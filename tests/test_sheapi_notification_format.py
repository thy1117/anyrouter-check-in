

def test_independent_list_inclusion():
	target_providers = ('xiaobai', 'sheapi', 'aiaiai', 'nianhua', 'twinkle')
	for provider in target_providers:
		check_in_success_content = []
		if provider in target_providers:
			check_in_success_content.append(f'✅ {provider}-acc · 今日已签到')
		assert len(check_in_success_content) == 1
