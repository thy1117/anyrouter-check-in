

def test_independent_list_grouping_and_ordering():
	check_in_success_items = [
		{'provider': 'twinkle', 'name': 'Twinkle-Dodo'},
		{'provider': 'sheapi', 'name': 'SheApi-thy1121'},
		{'provider': 'nianhua', 'name': 'Nianhua-Dodo'},
		{'provider': 'sheapi', 'name': 'SheApi-thy1118'},
		{'provider': 'xiaobai', 'name': '小白Code-1125'},
		{'provider': 'xiaobai', 'name': '小白Code-5237'},
		{'provider': 'twinkle', 'name': 'Twinkle-112581647'},
		{'provider': 'sheapi', 'name': 'SheApi-thy1117'},
	]

	summary = []
	summary.extend(['', '✅ 独立签到成功'])
	provider_order = ['xiaobai', 'sheapi', 'aiaiai', 'nianhua', 'twinkle']
	grouped_items = {}
	for item in check_in_success_items:
		p = item['provider']
		grouped_items.setdefault(p, []).append(item['name'])

	sorted_providers = sorted(
		grouped_items.keys(), key=lambda x: provider_order.index(x) if x in provider_order else 999
	)
	for idx, p in enumerate(sorted_providers):
		if idx > 0:
			summary.append('')
		for acc_name in sorted(grouped_items[p]):
			summary.append(f'✅ {acc_name} · 今日已签到')

	expected = [
		'',
		'✅ 独立签到成功',
		'✅ 小白Code-1125 · 今日已签到',
		'✅ 小白Code-5237 · 今日已签到',
		'',
		'✅ SheApi-thy1117 · 今日已签到',
		'✅ SheApi-thy1118 · 今日已签到',
		'✅ SheApi-thy1121 · 今日已签到',
		'',
		'✅ Nianhua-Dodo · 今日已签到',
		'',
		'✅ Twinkle-112581647 · 今日已签到',
		'✅ Twinkle-Dodo · 今日已签到',
	]
	assert summary == expected
