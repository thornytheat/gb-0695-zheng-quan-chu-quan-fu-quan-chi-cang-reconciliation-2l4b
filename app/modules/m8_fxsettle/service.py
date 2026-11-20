import json, hashlib, sqlite3, time
from decimal import Decimal
from app.store import connection
from app.errors import DomainError
from app.config import RULE_VERSION
from app.modules.m8_fxsettle import rules

def _now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def batch_id(action):
    # 幂等键只由行动标识决定，与规则版本无关：同一笔公司行动永远是同一个批次
    return hashlib.sha256(('fx:' + action).encode('utf-8')).hexdigest()[:16]

def _must_get(db, ident):
    row = db.execute('SELECT payload FROM fx_batches WHERE id=?', (ident,)).fetchone()
    if row is None: raise DomainError('NOT_FOUND', '汇兑批次不存在', 404)
    return json.loads(row['payload'])

def _payments(db, ident):
    rows = db.execute('SELECT payload FROM fx_payments WHERE batch_id=? ORDER BY client', (ident,)).fetchall()
    return [json.loads(r['payload']) for r in rows]

def _save_batch(db, batch):
    db.execute('UPDATE fx_batches SET payload=?, state=? WHERE id=?',
               (json.dumps(batch, ensure_ascii=False), batch['state'], batch['id']))

def _save_payment(db, ident, row):
    db.execute('UPDATE fx_payments SET payload=? WHERE batch_id=? AND client=?',
               (json.dumps(row, ensure_ascii=False), ident, row['客户']))

def _view(db, ident, reused=False):
    batch = _must_get(db, ident)
    payments = _payments(db, ident)
    total = rules.totals(payments, batch['bank_return'])
    return {'id': ident, 'action': batch['declaration']['action'], 'state': batch['state'],
            'declaration': batch['declaration'], 'rule_version': batch['rule_version'],
            'payments': [rules.display_row(p) for p in payments],
            'totals': rules.display_totals(total), 'history': batch['history'],
            'created_at': batch['created_at'], 'reused': reused}

def _declaration(decl):
    # 存储的规范化宣告还原为计算用Decimal结构
    return {'shares': {client: Decimal(amount) for client, amount in decl['shares'].items()},
            'dividend_hkd': Decimal(decl['dividend_hkd']), 'locked_rate': Decimal(decl['locked_rate'])}

def create(payload):
    decl = rules.parse_declaration(payload)
    ident = batch_id(decl['action'])
    with connection() as db:
        existing = db.execute('SELECT payload FROM fx_batches WHERE id=?', (ident,)).fetchone()
        if existing is not None:
            batch = json.loads(existing['payload'])
            if batch['declaration'] != rules.declaration_view(decl):
                # 同一行动绝不允许第二份宣告静默入账（防重复折算/重复支付）
                raise DomainError('CONFLICT', '同一行动已存在不同宣告，重跑被拒绝', 409)
            return _view(db, ident, reused=True)
        hkd = rules.entitlements(decl)
        locked = rules.convert(hkd, decl['locked_rate'])
        batch = {'id': ident, 'declaration': rules.declaration_view(decl), 'rule_version': RULE_VERSION,
                 'state': '待结汇', 'bank_return': None, 'created_at': _now(),
                 'history': [{'at': _now(), 'event': '创建批次',
                              'detail': '确认单锁定汇率 ' + str(decl['locked_rate']) + '，' + str(len(hkd)) + ' 户'}]}
        try:
            db.execute('INSERT INTO fx_batches(id, action_uid, payload, state, created_at) VALUES (?,?,?,?,?)',
                       (ident, decl['action'], json.dumps(batch, ensure_ascii=False), batch['state'], batch['created_at']))
            for client in sorted(hkd):
                row = rules.payment_row(decl, client, hkd[client], locked[client])
                # 主键 (批次, 客户) 兜底：任何路径下同一客户都不会出现第二笔支付
                db.execute('INSERT OR IGNORE INTO fx_payments(batch_id, client, payload) VALUES (?,?,?)',
                           (ident, client, json.dumps(row, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            return _view(db, ident, reused=True)
        return _view(db, ident)

def confirm_rate(ident, rate):
    if rate is None or (isinstance(rate, str) and not rate.strip()):
        raise DomainError('MISSING', '结汇汇率为空')
    rate = rules.positive(rate, '结汇汇率')
    with connection() as db:
        batch = _must_get(db, ident)
        if batch['state'] in ('已对账', '已发布'):
            raise DomainError('STATE', '批次已对账或已发布，采用汇率不可变更', 409)
        decl = _declaration(batch['declaration'])
        payments = _payments(db, ident)
        if batch['state'] == '待对账':
            current = payments[0]['采用汇率']
            if Decimal(current) == rate: return _view(db, ident, reused=True)
            batch['bank_return'] = None  # 汇率重订后原回款登记作废，须重新对账
            batch['history'].append({'at': _now(), 'event': '重订结汇汇率',
                                     'detail': '由 ' + current + ' 改为 ' + str(rate) + '，银行回款登记已清空'})
        else:
            batch['history'].append({'at': _now(), 'event': '确认结汇汇率', 'detail': '采用汇率 ' + str(rate)})
        hkd = rules.entitlements(decl)
        paid = rules.convert(hkd, rate)
        for row in payments:
            client = row['客户']
            updated = rules.payment_row(decl, client, hkd[client], Decimal(row['锁定_CNY']),
                                        actual_rate=rate, paid_cny=paid[client], state='待支付')
            _save_payment(db, ident, updated)
        batch['state'] = '待对账'
        _save_batch(db, batch)
        return _view(db, ident)

def bank_return(ident, amount):
    if amount is None or (isinstance(amount, str) and not amount.strip()):
        raise DomainError('MISSING', '银行回款金额为空')
    amount = rules.positive(amount, '银行回款')
    with connection() as db:
        batch = _must_get(db, ident)
        if batch['state'] == '待结汇':
            raise DomainError('STATE', '汇率未确认，不能登记银行回款', 409)
        if batch['state'] == '已发布':
            raise DomainError('STATE', '批次已发布，银行回款不可变更', 409)
        if batch['state'] == '已对账':
            if Decimal(batch['bank_return']) == amount: return _view(db, ident, reused=True)
            raise DomainError('STATE', '批次已对账，回款金额不一致', 409)
        batch['bank_return'] = str(amount)
        total = rules.totals(_payments(db, ident), batch['bank_return'])
        if total['对账差异_CNY'] == 0:
            batch['state'] = '已对账'
            batch['history'].append({'at': _now(), 'event': '银行回款对账相符', 'detail': '回款 ' + str(amount) + ' CNY'})
        else:
            batch['history'].append({'at': _now(), 'event': '银行回款对账差异',
                                     'detail': '回款 ' + str(amount) + '，差异 ' + str(total['对账差异_CNY']) + ' CNY'})
        _save_batch(db, batch)
        return _view(db, ident)

def publish(ident):
    with connection() as db:
        batch = _must_get(db, ident)
        if batch['state'] == '已发布': return _view(db, ident, reused=True)
        if batch['state'] != '已对账':
            raise DomainError('STATE', '整批未与银行回款对上，不能发布', 409)
        for row in _payments(db, ident):
            row['状态'] = '已付'
            _save_payment(db, ident, row)
        batch['state'] = '已发布'
        batch['history'].append({'at': _now(), 'event': '发布批次', 'detail': '全部支付标记为已付'})
        _save_batch(db, batch)
        return _view(db, ident)

def get(ident):
    with connection() as db: return _view(db, ident)

def list_batches():
    with connection() as db:
        rows = db.execute('SELECT id, payload FROM fx_batches ORDER BY rowid DESC').fetchall()
        result = []
        for r in rows:
            batch = json.loads(r['payload'])
            payments = _payments(db, r['id'])
            total = rules.display_totals(rules.totals(payments, batch['bank_return']))
            result.append({'id': r['id'], 'action': batch['declaration']['action'],
                           'security': batch['declaration']['security'], 'state': batch['state'],
                           '客户数': len(payments), '锁定_CNY': total['锁定_CNY'], '实付_CNY': total['实付_CNY'],
                           'created_at': batch['created_at']})
        return result
