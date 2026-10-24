from checkin import filter_accounts_by_target
from utils.config import AccountConfig


def test_filter_accounts_empty_returns_all():
	accounts = [
		AccountConfig(cookies=None, name='Lunora-1125', provider='lunora'),
		AccountConfig(cookies=None, name='Sidrune-tthx1', provider='sidrune'),
	]
	assert filter_accounts_by_target(accounts, '') == accounts
	assert filter_accounts_by_target(accounts, '   ') == accounts


def test_filter_accounts_by_provider():
	accounts = [
		AccountConfig(cookies=None, name='Lunora-1125', provider='lunora'),
		AccountConfig(cookies=None, name='Lunora-5237', provider='lunora'),
		AccountConfig(cookies=None, name='Sidrune-tthx1', provider='sidrune'),
		AccountConfig(cookies=None, name='Nianhua-1117', provider='nianhua'),
	]
	filtered = filter_accounts_by_target(accounts, 'lunora')
	assert [acc.name for acc in filtered] == ['Lunora-1125', 'Lunora-5237']


def test_filter_accounts_by_exact_name_case_insensitive():
	accounts = [
		AccountConfig(cookies=None, name='Lunora-1125', provider='lunora'),
		AccountConfig(cookies=None, name='Sidrune-tthx1', provider='sidrune'),
		AccountConfig(cookies=None, name='Sidrune-tthx12', provider='sidrune'),
		AccountConfig(cookies=None, name='Nianhua-1117', provider='nianhua'),
	]
	filtered = filter_accounts_by_target(accounts, 'sidrune-tthx1')
	assert [acc.name for acc in filtered] == ['Sidrune-tthx1']


def test_filter_accounts_multiple_targets():
	accounts = [
		AccountConfig(cookies=None, name='Lunora-1125', provider='lunora'),
		AccountConfig(cookies=None, name='Lunora-5237', provider='lunora'),
		AccountConfig(cookies=None, name='Lunora-8746', provider='lunora'),
		AccountConfig(cookies=None, name='Lunora-9308', provider='lunora'),
		AccountConfig(cookies=None, name='Lunora-3069', provider='lunora'),
		AccountConfig(cookies=None, name='Sidrune-tthx1', provider='sidrune'),
		AccountConfig(cookies=None, name='Sidrune-tthx12', provider='sidrune'),
		AccountConfig(cookies=None, name='Nianhua-1117', provider='nianhua'),
		AccountConfig(cookies=None, name='Nianhua-thy1118', provider='nianhua'),
		AccountConfig(cookies=None, name='AnyRouter', provider='anyrouter'),
	]
	# Test comma-separated provider and exact names
	filtered = filter_accounts_by_target(accounts, 'Lunora, Sidrune-tthx1, Nianhua-1117')
	assert [acc.name for acc in filtered] == [
		'Lunora-1125',
		'Lunora-5237',
		'Lunora-8746',
		'Lunora-9308',
		'Lunora-3069',
		'Sidrune-tthx1',
		'Nianhua-1117',
	]
