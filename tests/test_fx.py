import json, pytest
from decimal import Decimal, ROUND_HALF_UP
from app.config import FX_SAMPLE

DECL = json.loads(FX_SAMPLE.read_text(encoding='utf-8-sig'))

def create(client, decl=DECL):
    return client.post('/api/fx/batches', json=decl)

def confirm(client, ident, rate='0.9234'):
    return client.post('/api/fx/batches/' + ident + '/rate', json={'rate': rate})

def bank(client, ident, amount):
    return client.post('/api/fx/batches/' + ident + '/bank-return', json={'amount': amount})

def paid_total(client, ident):
    return Decimal(client.get('/api/fx/batches/' + ident).get_json()['totals']['实付_CNY'])

@pytest.fixture
def batch(client):
    return create(client).get_json()

# 创建与可复核结果
def test_create_state_is_rate_pending(batch):
    assert batch['state'] == '待结汇' and batch['reused'] is False

def test_entitled_hkd_exact_per_client(batch):
    first = batch['payments'][0]
    assert first['客户'] == 'C10001'
    assert Decimal(first['应得_HKD']) == Decimal('102') * Decimal('0.135')

def test_locked_amounts_sum_to_rounded_total(batch):
    locked = [Decimal(p['锁定_CNY']) for p in batch['payments']]
    hkd_total = sum(Decimal(p['应得_HKD']) for p in batch['payments'])
    assert sum(locked) == (hkd_total * Decimal('0.9123')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    assert Decimal(batch['totals']['锁定_CNY']) == sum(locked)

def test_rate_pending_has_no_paid_fields(batch):
    for p in batch['payments']:
        assert p['状态'] == '待结汇' and p['采用汇率'] == '' and p['实付_CNY'] == '' and p['差异_CNY'] == ''
    assert batch['totals']['实付_CNY'] == '' and batch['totals']['差异_CNY'] == ''

# 幂等：重跑不能重复支付
def test_rerun_same_declaration_reuses_batch(client, batch):
    again = create(client).get_json()
    assert again['id'] == batch['id'] and again['reused'] is True
    assert len(again['payments']) == len(batch['payments'])
    assert len(client.get('/api/fx/batches').get_json()) == 1

def test_rerun_after_publish_adds_no_payment(client, batch):
    ident = batch['id']
    confirm(client, ident)
    bank(client, ident, str(paid_total(client, ident)))
    client.post('/api/fx/batches/' + ident + '/publish', json={})
    again = create(client).get_json()
    assert again['state'] == '已发布' and len(again['payments']) == len(DECL['holdings'])
    assert sum(p['状态'] == '已付' for p in again['payments']) == len(DECL['holdings'])

def test_conflicting_declaration_rejected(client, batch):
    changed = dict(DECL, dividend_hkd='0.2')
    assert create(client, changed).status_code == 409

def test_rerun_with_extra_client_rejected(client, batch):
    more = dict(DECL, holdings=DECL['holdings'] + [{'client': 'C99999', 'shares': '1'}])
    assert create(client, more).status_code == 409
    assert len(create(client).get_json()['payments']) == len(DECL['holdings'])

def test_same_action_different_security_rejected(client, batch):
    assert create(client, dict(DECL, security='00999')).status_code == 409

# 汇率未确认时不能写成已付
def test_publish_blocked_before_rate_confirmed(client, batch):
    assert client.post('/api/fx/batches/' + batch['id'] + '/publish', json={}).status_code == 409

def test_bank_return_blocked_before_rate_confirmed(client, batch):
    response = bank(client, batch['id'], '100')
    assert response.status_code == 409 and response.get_json()['code'] == 'STATE'

# 汇率确认：实付、差异与勾稽
def test_confirm_rate_computes_paid_and_diff(client, batch):
    result = confirm(client, batch['id']).get_json()
    assert result['state'] == '待对账'
    hkd_total = sum(Decimal(p['应得_HKD']) for p in result['payments'])
    paid = [Decimal(p['实付_CNY']) for p in result['payments']]
    assert sum(paid) == (hkd_total * Decimal('0.9234')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    for p in result['payments']:
        assert p['状态'] == '待支付' and p['采用汇率'] == '0.9234'
        assert Decimal(p['锁定_CNY']) + Decimal(p['差异_CNY']) == Decimal(p['实付_CNY'])
    totals = result['totals']
    assert Decimal(totals['锁定_CNY']) + Decimal(totals['差异_CNY']) == Decimal(totals['实付_CNY'])

def test_confirm_same_rate_is_idempotent(client, batch):
    first = confirm(client, batch['id']).get_json()
    again = confirm(client, batch['id']).get_json()
    assert again['reused'] is True and again['payments'] == first['payments']

def test_equivalent_rate_scale_is_idempotent(client, batch):
    ident = batch['id']
    confirm(client, ident, '0.9234')
    again = confirm(client, ident, '0.92340').get_json()
    assert again['reused'] is True and again['state'] == '待对账'
    assert not any(h['event'] == '重订结汇汇率' for h in again['history'])

def test_equivalent_bank_return_scale_is_idempotent(client, batch):
    ident = batch['id']
    confirm(client, ident)
    total = paid_total(client, ident)
    bank(client, ident, str(total))
    again = bank(client, ident, str(total) + '0').get_json()
    assert again['reused'] is True and again['state'] == '已对账'

def test_tail_difference_allocated_by_largest_remainder(client):
    decl = {'action': 'TAIL-CASE', 'security': '00001', 'ex_date': '2026-11-18',
            'dividend_hkd': '1', 'locked_rate': '0.5',
            'holdings': [{'client': c, 'shares': '1'} for c in ('A', 'B', 'C')]}
    ident = create(client, decl).get_json()['id']
    result = confirm(client, ident, '0.005').get_json()
    paid = {p['客户']: Decimal(p['实付_CNY']) for p in result['payments']}
    # 每户 0.005 CNY，整批 0.015 → 0.02；分两户各一分，总额守恒
    assert paid == {'A': Decimal('0.01'), 'B': Decimal('0.01'), 'C': Decimal('0')}
    assert sum(paid.values()) == Decimal('0.02')

def test_reconfirm_different_rate_clears_bank_return(client, batch):
    ident = batch['id']
    confirm(client, ident)
    bank(client, ident, '1')
    result = confirm(client, ident, '0.9').get_json()
    assert result['totals']['银行回款_CNY'] == '' and result['state'] == '待对账'
    assert all(p['采用汇率'] == '0.9' for p in result['payments'])
    assert any(h['event'] == '重订结汇汇率' for h in result['history'])

# 整批对账与发布
def test_reconcile_mismatch_stays_unpublished(client, batch):
    ident = batch['id']
    confirm(client, ident)
    short = paid_total(client, ident) - Decimal('0.01')
    result = bank(client, ident, str(short)).get_json()
    assert result['state'] == '待对账' and result['totals']['对账差异_CNY'] == '-0.01'
    assert client.post('/api/fx/batches/' + ident + '/publish', json={}).status_code == 409

def test_reconcile_then_publish_marks_paid(client, batch):
    ident = batch['id']
    confirm(client, ident)
    result = bank(client, ident, str(paid_total(client, ident))).get_json()
    assert result['state'] == '已对账' and result['totals']['对账差异_CNY'] == '0.00'
    published = client.post('/api/fx/batches/' + ident + '/publish', json={}).get_json()
    assert published['state'] == '已发布'
    assert all(p['状态'] == '已付' for p in published['payments'])

def test_publish_is_idempotent(client, batch):
    ident = batch['id']
    confirm(client, ident)
    bank(client, ident, str(paid_total(client, ident)))
    client.post('/api/fx/batches/' + ident + '/publish', json={})
    again = client.post('/api/fx/batches/' + ident + '/publish', json={}).get_json()
    assert again['state'] == '已发布' and again['reused'] is True
    assert sum(p['状态'] == '已付' for p in again['payments']) == len(DECL['holdings'])

def test_published_batch_is_frozen(client, batch):
    ident = batch['id']
    confirm(client, ident)
    bank(client, ident, str(paid_total(client, ident)))
    client.post('/api/fx/batches/' + ident + '/publish', json={})
    assert confirm(client, ident, '0.9').status_code == 409
    assert bank(client, ident, '1').status_code == 409

def test_reconciled_rejects_different_return(client, batch):
    ident = batch['id']
    confirm(client, ident)
    total = str(paid_total(client, ident))
    bank(client, ident, total)
    assert bank(client, ident, total).get_json()['reused'] is True
    assert bank(client, ident, '1').status_code == 409

# 证据与页面
def test_evidence_csv_has_per_client_and_totals(client, batch):
    ident = batch['id']
    confirm(client, ident)
    bank(client, ident, str(paid_total(client, ident)))
    text = client.get('/api/fx/batches/' + ident + '/evidence.csv').data.decode('utf-8-sig')
    assert '应得_HKD' in text and '采用汇率' in text and '实付_CNY' in text and '差异_CNY' in text
    assert 'C10001' in text and '合计' in text and ident in text

def test_fx_page_renders(client):
    assert client.get('/fx').status_code == 200

def test_fx_link_on_main_page(client):
    assert '/fx' in client.get('/').data.decode()

# 输入校验
def test_unknown_batch_404(client):
    assert client.get('/api/fx/batches/absent').status_code == 404
    assert confirm(client, 'absent').status_code == 404

def test_invalid_declarations(client):
    assert create(client, dict(DECL, dividend_hkd='-1')).status_code == 422
    assert create(client, dict(DECL, locked_rate='0')).status_code == 422
    assert create(client, dict(DECL, holdings=[])).status_code == 422
    assert create(client, dict(DECL, ex_date='not-a-date')).status_code == 422
    dup = dict(DECL, holdings=[{'client': 'A', 'shares': '1'}, {'client': 'A', 'shares': '2'}])
    assert create(client, dup).status_code == 422
    bad = dict(DECL, holdings=[{'client': 'A', 'shares': '1.5'}])
    assert create(client, bad).status_code == 422

def test_invalid_rate_and_amount(client, batch):
    assert confirm(client, batch['id'], '-0.5').status_code == 422
    assert confirm(client, batch['id'], '').status_code == 422
    confirm(client, batch['id'])
    assert bank(client, batch['id'], 'abc').status_code == 422
    assert bank(client, batch['id'], None).status_code == 422

def test_non_object_body_rejected(client):
    assert client.post('/api/fx/batches', json=[1, 2]).status_code == 400
    assert client.post('/api/fx/batches', data='null', content_type='application/json').status_code == 400
