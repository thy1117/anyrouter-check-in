from checkin import format_check_in_notification


def test_independent_list_grouping_and_ordering_with_balance():
	check_in_success_items = [
		{'provider': 'sheapi', 'name': 'SheApi-thy1118', 'account_key': 'acc_1'},
		{'provider': 'sheapi', 'name': 'SheApi-thy1117', 'account_key': 'acc_2'},
		{'provider': 'xiaobai', 'name': '小白Code-1125', 'account_key': 'acc_3'},
	]
	account_check_in_details = {
		'acc_1': {
			'name': 'SheApi-thy1118',
			'after_quota': 1.34,
			'check_in_reward': 0.50,
			'usage_increase': 0.0,
			'success': True,
		},
		'acc_2': {
			'name': 'SheApi-thy1117',
			'after_quota': 8.63,
			'check_in_reward': 0.0,
			'usage_increase': 0.0,
			'success': True,
		},
		# xiaobai 没有 balance 接口，不在 account_check_in_details
	}

	summary = []
	summary.extend(['', '✅ 独立签到成功'])
	provider_order = ['xiaobai', 'sheapi', 'superapi', 'aiaiai', 'nianhua', 'twinkle']
	grouped_items = {}
	for item in check_in_success_items:
		p = item['provider']
		grouped_items.setdefault(p, []).append(item)

	sorted_providers = sorted(
		grouped_items.keys(), key=lambda x: provider_order.index(x) if x in provider_order else 999
	)
	for idx, p in enumerate(sorted_providers):
		if idx > 0:
			summary.append('')
		for item in sorted(grouped_items[p], key=lambda x: x['name']):
			acc_key = item.get('account_key')
			if acc_key and acc_key in account_check_in_details:
				summary.append(format_check_in_notification(account_check_in_details[acc_key]))
			else:
				summary.append(f'✅ {item["name"]} · 今日已签到')

	expected = [
		'',
		'✅ 独立签到成功',
		'✅ 小白Code-1125 · 今日已签到',
		'',
		'✅ SheApi-thy1117｜余额 $8.63｜签到无变化',
		'✅ SheApi-thy1118｜余额 $1.34｜签到 +$0.50',
	]
	assert summary == expected
