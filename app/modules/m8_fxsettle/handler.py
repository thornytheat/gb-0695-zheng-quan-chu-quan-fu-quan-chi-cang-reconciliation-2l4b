import csv, io
from flask import Blueprint, request, jsonify, Response
from app.errors import DomainError
from app.modules.m8_fxsettle import service

bp = Blueprint('fx', __name__)

def _body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict): raise DomainError('REQUEST', '请求内容必须为JSON对象', 400)
    return payload

@bp.route('/api/fx/batches', methods=['GET', 'POST'])
def batches():
    if request.method == 'POST': return jsonify(service.create(_body()))
    return jsonify(service.list_batches())

@bp.route('/api/fx/batches/<ident>')
def detail(ident): return jsonify(service.get(ident))

@bp.route('/api/fx/batches/<ident>/rate', methods=['POST'])
def rate(ident): return jsonify(service.confirm_rate(ident, _body().get('rate')))

@bp.route('/api/fx/batches/<ident>/bank-return', methods=['POST'])
def bank_return(ident): return jsonify(service.bank_return(ident, _body().get('amount')))

@bp.route('/api/fx/batches/<ident>/publish', methods=['POST'])
def publish(ident): return jsonify(service.publish(ident))

@bp.route('/api/fx/batches/<ident>/evidence.csv')
def evidence(ident):
    result = service.get(ident)
    out = io.StringIO(newline='')
    rows = [dict(p) for p in result['payments']]
    rows.append({'客户': '合计', **{k: v for k, v in result['totals'].items() if k in ('应得_HKD', '锁定_CNY', '实付_CNY', '差异_CNY')}})
    fields = ['批次', '行动', '批次状态', '规则版本', '客户', '股份', '每股分红_HKD', '应得_HKD', '锁定汇率', '锁定_CNY',
              '采用汇率', '实付_CNY', '差异_CNY', '状态', '银行回款_CNY', '对账差异_CNY']
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({'批次': result['id'], '行动': result['action'], '批次状态': result['state'],
                         '规则版本': result['rule_version'], '银行回款_CNY': result['totals']['银行回款_CNY'],
                         '对账差异_CNY': result['totals']['对账差异_CNY'], **row})
    return Response('\ufeff' + out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=fx-evidence.csv'})
