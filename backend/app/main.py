import asyncio
import json
import os
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from . import auth
from .bootstrap import initialize
from .db import Session, Asset, Strategy, Provider, Job, Account, Order, Setting, Audit, InvestmentSignal, serialize, audit, now
from .schemas import AssetInput, AssetBatch, InvestmentSignalInput, InvestmentSignalBatch, StrategyInput, StrategyTemplateInput, StrategyRevisionInput, RunInput, OptimizationInput, ProviderInput, RiskInput, ScheduleInput
from .optimization import enqueue_optimization
from .strategy_templates import list_templates, instantiate_template
from .strategy_revision import derive_strategy
from .services import enqueue, confirm_order
from .providers import load_news, load_bars
from .report_chart import report_with_prices
from .signal_diagnostics import report_with_signal_diagnostics


@asynccontextmanager
async def lifespan(app):
    auth.config()
    initialize()
    yield


app = FastAPI(title='Northstar Quant API', version='0.1.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
router = APIRouter(prefix='/api', dependencies=[Depends(auth.require_auth)])


@app.middleware('http')
async def headers(request, call_next):
    if int(request.headers.get('content-length', '0') or '0') > 200000:
        return JSONResponse({'detail': '请求过大'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse({'detail': '数据重复或发生并发变更，请刷新后重试'}, status_code=409)


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '0.1.0', 'real_trading': False}


class LoginInput(BaseModel):
    password: str = Field(max_length=500)


@app.post('/api/login')
def login(body: LoginInput, request: Request):
    return auth.login(body.password, request.client.host if request.client else 'local')


@router.get('/state')
def state():
    def strategy_summary(row):
        value = serialize(row)
        provenance = value.get('provenance') or {}
        features = provenance.get('feature_snapshot')
        if features:
            value['provenance'] = {**provenance, 'feature_snapshot': {
                key:item for key,item in features.items() if key != 'records'
            } | {'record_count':len(features.get('records', []))}}
        return value
    with Session() as db:
        jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(80)).all()
        return {'assets': [serialize(x) for x in db.scalars(select(Asset).order_by(Asset.market, Asset.symbol))],
                'strategies': [strategy_summary(x) for x in db.scalars(select(Strategy).order_by(Strategy.created_at.desc()))],
                'strategy_templates': list_templates(),
                'providers': [serialize(x) for x in db.scalars(select(Provider).order_by(Provider.priority))],
                'accounts': [serialize(x) for x in db.scalars(select(Account))],
                'orders': [serialize(x) for x in db.scalars(select(Order).order_by(Order.created_at.desc()).limit(100))],
                'jobs': [{k: v for k, v in serialize(x).items() if k not in {'payload', 'result'}} | {
                    'summary': {k: x.result.get(k) for k in ['symbol', 'metrics', 'data_mode']},
                    'spec': {key:value for key,value in x.payload.get('spec', {}).items() if key != 'feature_records'}} for x in jobs],
                'risk': db.get(Setting, 'risk').value, 'schedule': db.get(Setting, 'schedule').value,
                'capabilities': {'real_trading': False, 'arbitrary_python': False, 'model_budget': '单次研究最多4个模型；自动优化最多12轮、每轮1次模型调用、每次最多16000输出token、总时限30分钟；无隐式付费重试',
                                 'execution': '简化日频模拟；真实券商待接入验收'}, 'server_time': now()}


@router.get('/strategy-monitor')
def monitor_strategies():
    from .strategy_monitor import strategy_monitor
    with Session() as db:
        return strategy_monitor(db)


@router.get('/paper-portfolio')
def paper_portfolio(mode: str, currency: str):
    from .paper_portfolio import paper_portfolio as snapshot
    with Session() as db:
        return snapshot(db, mode, currency)


@router.get('/events')
async def events(request: Request):
    async def stream():
        for _ in range(600):
            if await request.is_disconnected():
                return
            yield 'event: refresh\ndata: ' + json.dumps({'at': now()}) + '\n\n'
            await asyncio.sleep(3)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@router.get('/investment-signals')
def investment_signals():
    with Session() as db:
        return [serialize(row) for row in db.scalars(
            select(InvestmentSignal).order_by(InvestmentSignal.available_at.desc(), InvestmentSignal.id.desc()).limit(1000))]


def signal_values(db, body: InvestmentSignalInput):
    values = body.model_dump(mode='json')
    asset = db.get(Asset, body.asset_id) if body.asset_id else None
    if body.asset_id and not asset:
        raise ValueError('选择的投资标的不存在')
    if body.kind == 'feature':
        from .research_features import FeatureObservation
        FeatureObservation.model_validate({
            'name': body.feature_name, 'category': body.category,
            'symbol': asset.symbol if asset else '*',
            'observed_at': body.observed_at, 'available_at': body.available_at,
            'value': body.value, 'source': body.source, 'max_age_days': body.max_age_days,
        })
        existing = db.scalars(select(InvestmentSignal).where(
            InvestmentSignal.kind == 'feature', InvestmentSignal.feature_name == body.feature_name)).all()
        for record in existing:
            if (record.category, record.asset_id, record.source, record.max_age_days) != (
                    body.category, body.asset_id, body.source, body.max_age_days):
                raise ValueError('同名数值特征须使用一致的标的、类别、来源和有效期')
            if (record.observed_at, record.available_at) == (
                    values['observed_at'], values['available_at']):
                raise ValueError('相同观测时间和首次可用时间的特征版本已存在')
    return values


@router.post('/investment-signals', status_code=201)
def create_investment_signal(body: InvestmentSignalInput):
    with Session.begin() as db:
        row = InvestmentSignal(**signal_values(db, body))
        db.add(row)
        db.flush()
        audit(db, 'investment_signal.created', signal_id=row.id, kind=row.kind)
        return serialize(row)


@router.post('/investment-signals/import', status_code=201)
def import_investment_signals(body: InvestmentSignalBatch):
    with Session.begin() as db:
        for item in body.records:
            db.add(InvestmentSignal(**signal_values(db, item)))
            db.flush()
        audit(db, 'investment_signal.imported', count=len(body.records))
        return {'count': len(body.records)}


@router.delete('/investment-signals/{signal_id}')
def delete_investment_signal(signal_id: str):
    with Session.begin() as db:
        row = db.get(InvestmentSignal, signal_id)
        if not row:
            raise HTTPException(404, '投资信号不存在')
        db.delete(row)
        audit(db, 'investment_signal.deleted', signal_id=signal_id, kind=row.kind)
        return {'deleted': True}


@router.get('/investment-signals/features')
def investment_signal_features():
    from .research_features import freeze_features
    with Session() as db:
        rows = db.scalars(select(InvestmentSignal).where(InvestmentSignal.kind == 'feature')
                          .order_by(InvestmentSignal.available_at, InvestmentSignal.id).limit(5001)).all()
        if len(rows) > 5000:
            raise ValueError('特征记录超过5000条，请先归档，再导出用于单次研究')
        assets = {row.id: row.symbol for row in db.scalars(select(Asset))}
        records = []
        for row in rows:
            if row.asset_id and row.asset_id not in assets:
                raise ValueError('特征关联的投资标的已不存在，无法导出')
            records.append({'name': row.feature_name, 'category': row.category,
                            'symbol': assets[row.asset_id] if row.asset_id else '*',
                            'observed_at': row.observed_at, 'available_at': row.available_at,
                            'value': row.value, 'source': row.source, 'max_age_days': row.max_age_days})
        return freeze_features(records)


@router.post('/assets')
def create_asset(body: AssetInput):
    with Session.begin() as db:
        row = Asset(**body.model_dump(), currency={'CN': 'CNY', 'HK': 'HKD', 'US': 'USD'}[body.market])
        db.add(row)
        db.flush()
        audit(db, 'asset.created', asset_id=row.id)
        return serialize(row)


@router.post('/assets/import')
def import_assets(body: AssetBatch):
    with Session.begin() as db:
        for item in body.assets:
            db.add(Asset(**item.model_dump(), currency={'CN': 'CNY', 'HK': 'HKD', 'US': 'USD'}[item.market]))
        db.flush()
        audit(db, 'assets.imported', count=len(body.assets))
        return {'count': len(body.assets)}


@router.put('/assets/{asset_id}')
def update_asset(asset_id: str, body: AssetInput):
    with Session.begin() as db:
        row = db.get(Asset, asset_id)
        if not row:
            raise HTTPException(404, '标的不存在')
        if (body.symbol, body.market, body.lot) != (row.symbol, row.market, row.lot):
            raise ValueError('标的代码、市场与交易单位不可原地更改，请停用后新建标的')
        row.name, row.enabled, row.tags = body.name, body.enabled, body.tags
        if 'holding_quantity' in body.model_fields_set:
            row.holding_quantity = body.holding_quantity
        audit(db, 'asset.updated', asset_id=row.id, enabled=row.enabled, holding_quantity=row.holding_quantity)
        return serialize(row)


@router.post('/strategy-templates/{template_id}/instantiate', status_code=201)
def create_template_strategy(template_id: str, body: StrategyTemplateInput):
    with Session.begin() as db:
        return serialize(instantiate_template(db, template_id, body))


@router.post('/strategies')
def create_strategy(body: StrategyInput):
    with Session.begin() as db:
        parent = db.get(Strategy, body.parent_id) if body.parent_id else None
        if body.parent_id and not parent:
            raise ValueError('父策略不存在')
        inherited = {key:deepcopy(parent.provenance[key]) for key in ('feature_snapshot', 'rebalance_every')
                     if parent and key in (parent.provenance or {})}
        row = Strategy(**body.model_dump(), provenance=inherited)
        db.add(row)
        db.flush()
        audit(db, 'strategy.created', strategy_id=row.id)
        return serialize(row)


@router.post('/strategies/{strategy_id}/approve')
def approve_strategy(strategy_id: str):
    with Session.begin() as db:
        row = db.get(Strategy, strategy_id)
        if not row:
            raise HTTPException(404, '策略不存在')
        reports = db.scalars(select(Job).where(Job.status == 'succeeded', Job.kind.in_(['backtest', 'research', 'optimization']))).all()
        direct = any(j.payload.get('strategy', {}).get('id') == strategy_id and j.kind == 'backtest' for j in reports)
        researched = any(j.result.get('research', {}).get('candidate_id') == strategy_id for j in reports)
        optimized = any(j.kind == 'optimization' and j.result.get('optimization', {}).get('candidate_id') == strategy_id
                        and j.result['optimization'].get('test_metrics') for j in reports)
        if not direct and not researched and not optimized:
            raise ValueError('批准前至少完成一次该版本的回测或独立研究评估')
        row.status = 'approved'
        audit(db, 'strategy.approved', strategy_id=row.id)
        return serialize(row)


@router.post('/strategies/{strategy_id}/revoke')
def revoke_strategy(strategy_id: str):
    with Session.begin() as db:
        row = db.get(Strategy, strategy_id)
        if not row:
            raise HTTPException(404, '策略不存在')
        row.status = 'candidate'
        audit(db, 'strategy.revoked', strategy_id=row.id)
        return serialize(row)


@router.post('/providers')
def create_provider(body: ProviderInput):
    with Session.begin() as db:
        row = Provider(**body.model_dump())
        db.add(row)
        db.flush()
        audit(db, 'provider.created', provider_id=row.id)
        return serialize(row)


@router.put('/providers/{provider_id}')
def update_provider(provider_id: str, body: ProviderInput):
    with Session.begin() as db:
        row = db.get(Provider, provider_id)
        if not row:
            raise HTTPException(404, '接口不存在')
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        audit(db, 'provider.updated', provider_id=row.id)
        return serialize(row)


@router.post('/jobs', status_code=202)
def create_job(body: RunInput):
    with Session.begin() as db:
        row = enqueue(db, body)
        return {'id': row.id, 'status': row.status}


@router.post('/optimizations', status_code=202)
def create_optimization(body: OptimizationInput):
    with Session.begin() as db:
        row = enqueue_optimization(db, body)
        return {'id': row.id, 'status': row.status}


@router.get('/jobs/{job_id}')
def get_job(job_id: str):
    with Session() as db:
        row = db.get(Job, job_id)
        if not row:
            raise HTTPException(404, '任务不存在')
        response = serialize(row)
        response['result'] = report_with_prices(response.get('result'))
        if row.status == 'succeeded' and row.kind in ('backtest', 'research'):
            response['result'] = report_with_signal_diagnostics(response['result'], row.payload)
        return response


@router.post('/jobs/{job_id}/revise-strategy', status_code=202)
def revise_strategy(job_id: str, body: StrategyRevisionInput):
    with Session.begin() as db:
        job = derive_strategy(db, job_id, body)
        return {'id': job.id, 'strategy_id': job.payload['strategy']['id'],
                'baseline_job_id': job_id, 'status': job.status}


@router.post('/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    with Session.begin() as db:
        count = db.execute(update(Job).where(Job.id == job_id, Job.status.in_(['queued', 'running'])).values(
            status='cancelled', finished_at=now())).rowcount
        if count != 1:
            raise ValueError('任务不在可取消状态')
        audit(db, 'job.cancelled', job_id=job_id)
        return {'status': 'cancelled'}


@router.post('/jobs/{job_id}/retry', status_code=202)
def retry_job(job_id: str):
    with Session.begin() as db:
        row = db.get(Job, job_id)
        if not row or row.status not in ['failed', 'cancelled'] or 'spec' not in row.payload:
            raise ValueError('此任务不能重试，请创建新任务')
        if row.kind == 'optimization':
            raise ValueError('自动优化可能产生模型费用；请在策略区确认预算后新建，不自动付费重试')
        # Fresh validation but original snapshot retained. No implicit live replay.
        spec = RunInput(**row.payload['spec'])
        if spec.kind == 'signal':
            raise ValueError('交易信号请重新创建，以重新获取行情和账户状态')
        new = Job(kind=row.kind, payload=row.payload)
        db.add(new)
        db.flush()
        audit(db, 'job.retried', old_id=job_id, new_id=new.id)
        return {'id': new.id, 'status': new.status}


@router.post('/orders/{order_id}/confirm')
def confirm(order_id: str):
    with Session.begin() as db:
        return serialize(confirm_order(db, order_id))


@router.post('/orders/{order_id}/cancel')
def cancel_order(order_id: str):
    with Session.begin() as db:
        count = db.execute(update(Order).where(Order.id == order_id, Order.status == 'pending').values(status='cancelled')).rowcount
        if count != 1:
            raise ValueError('订单不能取消')
        audit(db, 'order.cancelled', order_id=order_id)
        return serialize(db.get(Order, order_id))


@router.post('/live/orders')
def live_disabled():
    raise HTTPException(409, '实盘接口未接入和验收；当前系统禁止真实下单')


@router.put('/settings/risk')
def risk(body: RiskInput):
    with Session.begin() as db:
        db.get(Setting, 'risk').value = body.model_dump()
        audit(db, 'risk.updated', **body.model_dump())
        return body


@router.put('/settings/schedule')
def schedules(body: ScheduleInput):
    with Session.begin() as db:
        if body.daily_enabled or body.weekly_enabled:
            strategy, asset = db.get(Strategy, body.strategy_id), db.get(Asset, body.asset_id)
            if not strategy or not asset or not asset.enabled:
                raise ValueError('请选择有效策略和标的')
            if body.daily_enabled and strategy.status != 'approved':
                raise ValueError('日频信号需要已批准策略')
            if body.weekly_enabled and not body.model_ids:
                raise ValueError('周研究需要至少一个模型')
            active = {p.id for p in db.scalars(select(Provider).where(Provider.kind == 'model', Provider.enabled.is_(True)))}
            if set(body.model_ids) - active:
                raise ValueError('模型不可用')
        db.get(Setting, 'schedule').value = body.model_dump()
        audit(db, 'schedule.updated', **body.model_dump())
        return body


@router.get('/news')
def news():
    with Session() as db:
        return load_news([serialize(p) for p in db.scalars(select(Provider))], now())


@router.get('/assets/valuations')
def valuations():
    from .asset_valuation import asset_valuations
    with Session() as db:
        assets = [serialize(row) for row in db.scalars(select(Asset))]
        providers = [serialize(row) for row in db.scalars(select(Provider).order_by(Provider.priority))]
    return asset_valuations(assets, providers)


@router.get('/market')
def market(mode: str = 'demo'):
    if mode not in {'demo', 'historical', 'live'}:
        raise ValueError('无效数据环境')
    if mode != 'demo':
        from .index_quotes import index_quotes
        return index_quotes()
    end = datetime.now(timezone.utc).date()
    with Session() as db:
        providers = [serialize(p) for p in db.scalars(select(Provider))]
    rows = []
    for symbol, name in [('CN.000300', '沪深300'), ('HK.HSI', '恒生指数'), ('US.SPX', '标普500')]:
        try:
            bars, source = load_bars(providers, mode, symbol, (end - timedelta(days=100)).isoformat(), end.isoformat())
            prices = [b.close for b in bars]
            rows.append({'symbol': symbol, 'name': name, 'price': prices[-1], 'change': prices[-1] / prices[-2] - 1,
                         'trend': '高于20日均线' if prices[-1] > sum(prices[-20:]) / 20 else '低于20日均线',
                         'date': bars[-1].date, 'source': source['name'], 'synthetic': mode == 'demo', 'prices': prices[-30:]})
        except ValueError as exc:
            rows.append({'symbol': symbol, 'name': name, 'error': str(exc)})
    return rows


@router.get('/audit')
def audits():
    with Session() as db:
        return [serialize(x) for x in db.scalars(select(Audit).order_by(Audit.at.desc()).limit(100))]


app.include_router(router)
