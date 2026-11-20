from decimal import Decimal
from app.domain import allocate_cents, day
from app.numeric import number, fmt
from app.errors import DomainError

# 批次状态机：待结汇 → 待对账 → 已对账 → 已发布
BATCH_STATES = ('待结汇', '待对账', '已对账', '已发布')
# 支付状态：汇率未确认前只能是待结汇，发布后才可为已付
PAY_STATES = ('待结汇', '待支付', '已付')

def _required(payload, key, label):
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DomainError('MISSING', label + '为空')
    return value

def positive(value, label):
    result = number(value)
    if result <= 0: raise DomainError('RANGE', label + '必须为正数')
    return result

def parse_declaration(payload):
    # 宣告是幂等键的来源：同一行动标识只允许一份完全相同的宣告
    if not isinstance(payload, dict): raise DomainError('REQUEST', '请求内容必须为JSON对象', 400)
    action = str(_required(payload, 'action', '行动标识')).strip()
    security = str(_required(payload, 'security', '证券代码')).strip()
    ex_date = str(day(str(_required(payload, 'ex_date', '除权日')).strip()))
    dividend = positive(_required(payload, 'dividend_hkd', '每股分红_HKD'), '每股分红')
    locked = positive(_required(payload, 'locked_rate', '锁定汇率'), '锁定汇率')
    holdings = payload.get('holdings')
    if not isinstance(holdings, list) or not holdings: raise DomainError('MISSING', '持有名单为空')
    shares = {}
    for item in holdings:
        if not isinstance(item, dict): raise DomainError('TYPE', '持有记录必须为JSON对象')
        client = str(item.get('client') or '').strip()
        if not client: raise DomainError('MISSING', '客户号为空')
        if client in shares: raise DomainError('DUPLICATE', '同一客户在宣告中重复')
        amount = number(_required(item, 'shares', '持有股数'))
        if amount < 0 or amount != amount.to_integral_value(): raise DomainError('SHARES', '持仓须为非负整数')
        shares[client] = amount
    return {'action': action, 'security': security, 'ex_date': ex_date,
            'dividend_hkd': dividend, 'locked_rate': locked, 'shares': shares}

def declaration_view(decl):
    # 规范化视图用于宣告一致性比较（重跑判定），与输入键序、空白无关
    return {'action': decl['action'], 'security': decl['security'], 'ex_date': decl['ex_date'],
            'dividend_hkd': str(decl['dividend_hkd']), 'locked_rate': str(decl['locked_rate']),
            'shares': {client: str(decl['shares'][client]) for client in sorted(decl['shares'])}}

def entitlements(decl):
    # 每户应得港币 = 持有股数 × 每股分红，Decimal 精确乘积
    return {client: amount * decl['dividend_hkd'] for client, amount in decl['shares'].items()}

def convert(hkd, rate):
    # 整批总额四舍五入到分后按最大余额法分配到户：Σ每户 ≡ round(Σ应得×汇率, 分)，
    # 与银行对整批港币一次结汇的金额精确一致，尾差在户间显式分配而非沉淀在批差里
    return allocate_cents({client: value * rate for client, value in hkd.items()})

def payment_row(decl, client, hkd, locked_cny, actual_rate=None, paid_cny=None, state='待结汇'):
    row = {'客户': client, '股份': str(decl['shares'][client]), '每股分红_HKD': str(decl['dividend_hkd']),
           '应得_HKD': str(hkd), '锁定汇率': str(decl['locked_rate']), '锁定_CNY': str(locked_cny),
           '采用汇率': '', '实付_CNY': '', '差异_CNY': '', '状态': state}
    if actual_rate is not None:
        row.update({'采用汇率': str(actual_rate), '实付_CNY': str(paid_cny),
                    '差异_CNY': str(paid_cny - locked_cny), '状态': state})
    return row

def totals(payments, bank_return=None):
    # 勾稽关系：Σ锁定 + Σ差异 = Σ实付；对账差异 = 银行回款 − Σ实付
    result = {'应得_HKD': sum((Decimal(p['应得_HKD']) for p in payments), Decimal(0)),
              '锁定_CNY': sum((Decimal(p['锁定_CNY']) for p in payments), Decimal(0)),
              '实付_CNY': None, '差异_CNY': None, '银行回款_CNY': bank_return, '对账差异_CNY': None}
    if all(p['实付_CNY'] for p in payments):
        paid = sum((Decimal(p['实付_CNY']) for p in payments), Decimal(0))
        result['实付_CNY'] = paid
        result['差异_CNY'] = paid - result['锁定_CNY']
        if bank_return is not None:
            result['对账差异_CNY'] = Decimal(bank_return) - paid
    return result

def display_row(row):
    shown = dict(row)
    shown['应得_HKD'] = fmt(row['应得_HKD'])
    shown['锁定_CNY'] = fmt(row['锁定_CNY'], '0.01')
    for key in ('实付_CNY', '差异_CNY'):
        if row[key] != '': shown[key] = fmt(row[key], '0.01')
    return shown

def display_totals(result):
    shown = {'应得_HKD': fmt(result['应得_HKD']), '锁定_CNY': fmt(result['锁定_CNY'], '0.01')}
    for key in ('实付_CNY', '差异_CNY', '银行回款_CNY', '对账差异_CNY'):
        shown[key] = fmt(result[key], '0.01') if result[key] is not None else ''
    return shown
