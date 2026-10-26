from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.backtest import simulate
from app.contracts import Bar
from app.optimization import enqueue_optimization, persist_candidates, run_optimization
from app.schemas import OptimizationInput, ProviderInput


ZERO = 'def target(history, params):\n return 0.0'
LONG = 'def target(history, params):\n return params["weight"]'
OPTIONS = {'initial':100000, 'lot':1, 'fee_bps':5, 'slippage_bps':5, 'max_weight':.3}


def market():
    return [Bar((date(2024,1,1)+timedelta(days=index)).isoformat(), 100*math.exp(.005*index),
                102*math.exp(.005*index), 99*math.exp(.005*index), 101*math.exp(.005*index),10000) for index in range(200)]


def request(**overrides):
    return OptimizationInput(strategy_id='strategy',asset_id='asset',mode='demo',start='2024-01-01',end='2024-07-18',
                             model_ids=['model'],client_key='opt-test-key',**overrides)


def payload(**overrides):
    return {'spec':request(**overrides).model_dump(mode='json'),
            'strategy':{'id':'strategy','name':'original','code':ZERO,'params':{}},
            'providers':[{'id':'model','name':'model','enabled':True,'kind':'model','driver':'fake','config':{}}]}


def execute(monkeypatch,proposals,**overrides):
    from app import optimization
    contexts=[]
    iterator=iter(proposals)
    class Model:
        def __init__(self, config):
            self.config=config
        def propose(self,context):
            contexts.append(deepcopy(context))
            return next(iterator)
    monkeypatch.setitem(optimization.MODEL_DRIVERS,'fake',Model)
    p=payload(**overrides)
    bars=market()
    base={**simulate(bars,ZERO,{},**OPTIONS),'snapshot':'fixture.parquet','source':{'name':'fixture'},'symbol':'TEST','currency':'USD','data_mode':'demo'}
    checkpoints=[]
    result=run_optimization(p,bars,base,OPTIONS,lambda report:checkpoints.append(deepcopy(report)))
    return result,contexts,checkpoints,p


def test_model_naming_and_explanations_follow_selected_not_last_attempt(monkeypatch):
    explained = {'name':'定权持有 · 三成仓位', 'strategy_logic':'按 weight 配置保持目标仓位。',
                 'change_summary':'将空仓策略改为三成目标仓位。', 'naming_reason':'名称体现定权持有与仓位上限。',
                 'reason':'检验持有上涨资产的收益，承担方向风险。'}
    result, _, checkpoints, _ = execute(monkeypatch, [
        {'code':LONG, 'params':{'weight':.3}, **explained},
        {'code':ZERO, 'params':{}, **{**explained, 'name':'恢复空仓'}}], max_rounds=2, target_return=1)
    opt = result['optimization']
    assert opt['rounds'][0]['decision'] == 'keep'
    assert opt['rounds'][1]['decision'] == 'rollback'
    assert opt['selected']['name'] == explained['name']
    assert opt['selected']['strategy_logic'] == explained['strategy_logic']
    assert opt['selected']['naming_source'] == 'model'
    assert opt['rounds'][1]['parent_round'] == 1
    assert opt['rounds'][0]['notes'] == explained['reason']
    assert opt['rounds'][0]['change']['param_changes']['weight'] == {'before':None, 'after':.3}
    assert opt['rounds'][0]['prompt_version'] == 'optimization-v3'
    assert checkpoints[-1]['optimization']['selected']['naming_reason'] == explained['naming_reason']


def test_legacy_proposal_name_is_explicit_fallback(monkeypatch):
    result, _, _, _ = execute(monkeypatch, [{'code':LONG, 'params':{'weight':.3}}], max_rounds=1, target_return=1)
    assert result['optimization']['selected']['naming_source'] == 'fallback'


def test_invalid_explanation_type_rejected_without_changing_baseline(monkeypatch):
    result, _, _, _ = execute(monkeypatch, [{'code':LONG, 'params':{'weight':.3}, 'name':['invalid']}], max_rounds=1, target_return=1)
    assert result['optimization']['best_round'] == 0
    assert result['optimization']['rounds'][0]['status'] == 'invalid'


def test_auto_loop_stops_at_target_and_preserves_original(monkeypatch):
    result,contexts,checkpoints,p=execute(monkeypatch,[{'code':LONG,'params':{'weight':.3},'reason':'trend'}],target_return=.03)
    opt=result['optimization']
    assert len(opt['rounds'])==1 and opt['target_reached'] and opt['stop_reason']=='target_reached'
    assert opt['best_round']==1 and opt['test_metrics']['trade_count']>0
    assert opt['test_passed'] and result['metrics']['trade_count']>0
    assert p['strategy']['code']==ZERO and p['strategy']['params']=={}
    assert contexts[0]['validation_end']==market()[159].date
    assert all(bar['date']<=market()[159].date for bar in contexts[0]['prices'])
    assert all('test_metrics' not in context for context in contexts)
    assert checkpoints[0]['optimization']['rounds']==[]
    assert checkpoints[-1]['optimization']['phase']=='已完成'


def test_round_budget_includes_invalid_proposals_and_feeds_error_back(monkeypatch):
    invalid={'code':'import os','params':{},'reason':'invalid'}
    result,contexts,_,_=execute(monkeypatch,[invalid,{'code':ZERO,'params':{},'reason':'stay flat'}],max_rounds=2,target_return=1)
    opt=result['optimization']
    assert len(opt['rounds'])==len(contexts)==2
    assert opt['rounds'][0]['status']=='invalid'
    assert opt['rounds'][1]['status']=='evaluated'
    assert 'error' in contexts[1]['attempts'][0]
    assert opt['best_round']==0 and not opt['target_reached']
    assert opt['stop_reason']=='max_rounds'
    assert not opt['test_passed']


def test_default_baseline_does_not_use_full_period_to_skip_calls(monkeypatch):
    result,contexts,_,_=execute(monkeypatch,[{'code':ZERO,'params':{}}],max_rounds=1,target_return=.01)
    assert len(contexts)==1 and not result['optimization']['target_reached']


def test_test_prices_cannot_change_model_feedback_or_selection(monkeypatch):
    from app import optimization
    class Model:
        def __init__(self, config):
            pass
        def propose(self,context):
            contexts.append(deepcopy(context))
            return {'code':LONG,'params':{'weight':.3},'reason':'trend'}
    monkeypatch.setitem(optimization.MODEL_DRIVERS,'fake',Model)
    original=market()
    future=original[:160]+[replace(bar,open=bar.open*.5,high=bar.high*.5,low=bar.low*.5,close=bar.close*.5) for bar in original[160:]]
    results=[]
    prompts=[]
    for bars in (original,future):
        contexts=[]
        base=simulate(bars,ZERO,{},**OPTIONS)
        results.append(run_optimization(payload(target_return=1,max_rounds=2),bars,base,OPTIONS))
        prompts.append(contexts)
    assert prompts[0]==prompts[1]
    left,right=[result['optimization'] for result in results]
    assert left['best_round']==right['best_round']
    assert left['rounds']==right['rounds']
    assert left['best_validation_metrics']==right['best_validation_metrics']
    assert left['test_metrics']!=right['test_metrics']


def test_minimum_trades_prevents_fake_zero_risk_success(monkeypatch):
    result,_,_,_=execute(monkeypatch,[{'code':LONG,'params':{'weight':.00000001}}],max_rounds=1,target_return=.01)
    opt=result['optimization']
    assert not opt['target_reached'] and opt['best_validation_metrics']['trade_count']==0


def test_rejects_insufficient_history_before_model_call(monkeypatch):
    from app import optimization
    monkeypatch.setitem(optimization.MODEL_DRIVERS,'fake',lambda config:pytest.fail('must not call'))
    bars=market()[:149]
    with pytest.raises(ValueError,match='150'):
        run_optimization(payload(),bars,simulate(bars,ZERO,{}),OPTIONS)


def test_warmup_prices_allow_test_first_open_execution_without_past_profit():
    bars=market()
    code='def target(history, params):\n if len(history)<100:\n  return 0\n return .3'
    result=simulate(bars[160:],code,{},warmup_bars=bars[:160],**OPTIONS)
    assert result['trades'][0]['date']==bars[160].date
    assert result['trades'][0]['signal_date']==bars[159].date
    assert len(result['curve'])==40
    assert result['metrics']['total_return']<.1
    no_warmup=simulate(bars[160:],code,{},**OPTIONS)
    assert no_warmup['metrics']['trade_count']==0


def test_warmup_rejects_future_or_unsorted_prices():
    bars=market()
    with pytest.raises(ValueError,match='预热'):
        simulate(bars[160:],ZERO,{},warmup_bars=bars[:161])
    with pytest.raises(ValueError,match='预热'):
        simulate(bars[160:],ZERO,{},warmup_bars=list(reversed(bars[:160])))


@pytest.mark.parametrize('overrides',[{'target_return':0},{'target_return':-1},{'target_return':float('nan')},{'max_rounds':13},{'min_trades':0},{'max_output_tokens':99999},{'model_ids':[]},{'model_ids':['x','y','z']},{'mode':'live'}])
def test_optimization_budget_validation(overrides):
    data=request().model_dump()
    data.update(overrides)
    with pytest.raises(ValidationError):
        OptimizationInput(**data)


@pytest.fixture
def original_experiment(database,tmp_path,monkeypatch):
    from app.db import Asset,Job,Provider,Strategy
    from app import providers
    monkeypatch.setattr(providers,'DATA_ROOT',tmp_path)
    bars=market()
    result=simulate(bars,ZERO,{},**OPTIONS)
    result.update(snapshot='frozen.parquet',source={'name':'fixture','mode':'demo'},symbol='TEST',currency='USD',data_mode='demo')
    pq.write_table(pa.Table.from_pylist([asdict(bar) for bar in bars]),tmp_path/'frozen.parquet')
    with database.begin() as db:
        db.add(Asset(id='asset',symbol='TEST',name='TEST',market='US',currency='USD',lot=1))
        db.add(Strategy(id='strategy',name='original',code=ZERO,params={}))
        db.add(Provider(id='model',name='demo',kind='model',driver='demo_model',enabled=True,config={}))
        db.flush()
        spec=request(baseline_job_id='baseline')
        from app.services import snapshot
        frozen=snapshot(db,spec)
        frozen['frozen_data']={'path':result['snapshot'],'hash':result['data_hash'],'source':result['source']}
        db.add(Job(id='baseline',kind='backtest',status='succeeded',payload=frozen,result=result))
    return database,result,tmp_path/'frozen.parquet'


def test_enqueue_from_experiment_inherits_snapshot_and_is_idempotent(original_experiment):
    from app.db import Job
    database,report,_=original_experiment
    before=deepcopy(report)
    with database.begin() as db:
        first=enqueue_optimization(db,request(baseline_job_id='baseline'))
        second=enqueue_optimization(db,request(baseline_job_id='baseline'))
        assert first.id==second.id and first.kind=='optimization'
        assert first.payload['frozen_data']['hash']==report['data_hash']
        assert first.payload['strategy']['code']==ZERO
    with database() as db:
        assert db.get(Job,'baseline').result==before


def test_baseline_conditions_cannot_be_changed(original_experiment):
    database,_,_=original_experiment
    with database.begin() as db,pytest.raises(ValueError,match='沿用原始'):
        enqueue_optimization(db,request(baseline_job_id='baseline',fee_bps=0))


def test_bad_frozen_snapshot_never_falls_back_to_live(original_experiment,monkeypatch):
    from app import providers
    monkeypatch.setattr(providers,'load_bars',lambda *args:pytest.fail('no network fallback'))
    database,_,path=original_experiment
    path.write_bytes(b'corrupt')
    with database.begin() as db,pytest.raises(ValueError,match='快照'):
        enqueue_optimization(db,request(baseline_job_id='baseline'))


def test_candidates_saved_idempotently_as_unapproved_versions(original_experiment,monkeypatch):
    from app.db import Job,Strategy
    database,_,_=original_experiment
    result,_,_,_=execute(monkeypatch,[{'code':LONG,'params':{'weight':.3},'reason':'trend','usage':{'output_tokens':100},
                                     'name':'定权持有 · 风险限仓','strategy_logic':'固定目标仓位持有','naming_reason':'体现仓位约束'}])
    with database.begin() as db:
        job=enqueue_optimization(db,request(baseline_job_id='baseline'))
        result=persist_candidates(db,job,result)
        result=persist_candidates(db,job,result)
        identifier=result['optimization']['candidate_id']
        candidate=db.get(Strategy,identifier)
        assert candidate.status=='candidate' and candidate.parent_id=='strategy'
        assert candidate.name == '定权持有 · 风险限仓'
        assert candidate.provenance['naming_reason'] == '体现仓位约束'
        assert candidate.provenance['naming_source'] == 'model'
        assert candidate.provenance['test_metrics']==result['optimization']['test_metrics']
        assert result['strategy_id']==identifier
        assert len(db.scalars(select(Strategy).where(Strategy.parent_id=='strategy')).all())==1


def test_cannot_retry_optimization_implicitly(original_experiment):
    from app.main import retry_job
    database,_,_=original_experiment
    with database.begin() as db:
        job=enqueue_optimization(db,request(baseline_job_id='baseline'))
        job.status='cancelled'
        identifier=job.id
    with pytest.raises(ValueError,match='费用'):
        retry_job(identifier)


@pytest.mark.parametrize('api_format',['chat_completions','responses'])
def test_provider_protocol_and_budgets_validate(api_format):
    body=ProviderInput(name='test',kind='model',driver='openai_compatible',config={'base_url':'https://models.example/v1','model':'gpt','key_env':'MODEL_KEY_TEST','api_format':api_format,'reasoning_effort':'high','max_output_tokens':'6000','timeout_seconds':'90'})
    assert body.config['api_format']==api_format


def test_cancelled_progress_retains_candidates_without_changing_status(original_experiment,monkeypatch,tmp_path):
    import json
    from app.db import Job,Strategy
    from app.worker import save_optimization_progress
    database,_,_=original_experiment
    result,_,_,_=execute(monkeypatch,[{'code':LONG,'params':{'weight':.3},'reason':'trend'}])
    progress=tmp_path/'progress.json'
    progress.write_text(json.dumps(result))
    with database.begin() as db:
        job=enqueue_optimization(db,request(baseline_job_id='baseline'))
        job.status='cancelled'
        save_optimization_progress(db,job,progress)
        assert job.status=='cancelled'
        assert job.payload['frozen_data']['hash']==result['data_hash']
        assert db.get(Strategy,job.result['optimization']['candidate_id']).status=='candidate'


def test_iteration_time_budget_stops_without_extra_model_calls(monkeypatch):
    from app import optimization
    ticks=iter([0,120,121])
    monkeypatch.setattr(optimization.time,'monotonic',lambda:next(ticks))
    result,contexts,_,_=execute(monkeypatch,[],time_budget_seconds=120)
    assert contexts==[]
    assert result['optimization']['stop_reason']=='time_budget'


def test_model_presets_do_not_store_credentials(database,monkeypatch):
    from app.db import Provider
    from app.model_presets import install_model_presets
    monkeypatch.setenv('MODEL_KEY_STARCODE','fake-test-only-secret')
    with database.begin() as db:
        install_model_presets(db)
        db.flush()
        install_model_presets(db)
        presets=db.scalars(select(Provider).where(Provider.id.like('starcode-%'))).all()
        assert len(presets)==2
        assert all(provider.config['api_format']=='responses' for provider in presets)
        assert all(provider.config['key_env']=='MODEL_KEY_STARCODE' for provider in presets)
        assert 'fake-test-only-secret' not in str([provider.config for provider in presets])


def test_deepseek_presets_are_secret_free_idempotent_and_preserve_user_settings(database,monkeypatch):
    from app.db import Provider
    from app.model_presets import DEEPSEEK_BASE,DEEPSEEK_PROVIDER_ID,install_model_presets
    monkeypatch.delenv('MODEL_KEY_STARCODE',raising=False)
    monkeypatch.setenv('MODEL_KEY_DEEPSEEK','fake-deepseek-test-secret')
    with database.begin() as db:
        install_model_presets(db)
        db.flush()
        provider=db.get(Provider,DEEPSEEK_PROVIDER_ID)
        assert provider.enabled and provider.config['model']=='deepseek-flash'
        assert provider.config['base_url']==DEEPSEEK_BASE
        assert provider.config['api_format']=='chat_completions'
        assert provider.config['key_env']=='MODEL_KEY_DEEPSEEK'
        assert 'fake-deepseek-test-secret' not in str(provider.config)
        provider.enabled=False
        provider.config={**provider.config,'max_output_tokens':'3000'}
        install_model_presets(db)
        db.flush()
        assert not provider.enabled and provider.config['max_output_tokens']=='3000'
        assert len(db.scalars(select(Provider).where(Provider.id==DEEPSEEK_PROVIDER_ID)).all())==1


def test_deepseek_preset_requires_backend_secret(database,monkeypatch):
    from app.db import Provider
    from app.model_presets import DEEPSEEK_PROVIDER_ID,install_model_presets
    monkeypatch.delenv('MODEL_KEY_DEEPSEEK',raising=False)
    monkeypatch.delenv('MODEL_KEY_STARCODE',raising=False)
    with database.begin() as db:
        install_model_presets(db)
        db.flush()
        assert db.get(Provider,DEEPSEEK_PROVIDER_ID) is None


def test_worker_subprocess_completes_optimization_and_never_places_orders(database,tmp_path,monkeypatch):
    from sqlalchemy import create_engine,func
    from sqlalchemy.orm import sessionmaker
    from app.db import Base,Job,Strategy,Order,now
    from app import worker
    engine=create_engine('sqlite:///'+str(tmp_path/'worker.db'))
    Base.metadata.create_all(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(worker,'Session',sessions)
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'worker.db'))
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'))
    stamp=now()
    p=payload(target_return=1,max_rounds=2)
    p['spec']['end']='2024-12-31'
    p['providers']=[{'id':'model','kind':'model','name':'demo','enabled':True,'driver':'demo_model','priority':10,'config':{}},
                    {'id':'market','kind':'market','name':'demo data','enabled':True,'driver':'demo','priority':1,'config':{}}]
    p.update(asset={'symbol':'TEST','currency':'USD','lot':1},risk={'max_weight':.3})
    with sessions.begin() as db:
        db.add(Strategy(id='strategy',name='original',code=ZERO,params={}))
        db.add(Job(id='worker-test',kind='optimization',status='running',started_at=stamp,payload=p))
    worker.execute('worker-test',p,stamp)
    with sessions() as db:
        job=db.get(Job,'worker-test')
        assert job.status=='succeeded',job.error
        assert len(job.result['optimization']['rounds'])==2
        assert all(row['candidate_id'] for row in job.result['optimization']['rounds'])
        assert job.payload['frozen_data']['hash']==job.result['data_hash']
        assert db.scalar(select(func.count()).select_from(Order))==0
        assert all(strategy.status=='candidate' for strategy in db.scalars(select(Strategy)).all())
    engine.dispose()


def test_access_denied_stops_loop_immediately(monkeypatch):
    from app import optimization
    calls=[]
    class Denied:
        def __init__(self,config):
            pass
        def propose(self,context):
            calls.append(context)
            raise ValueError('模型服务返回 HTTP 403')
    monkeypatch.setitem(optimization.MODEL_DRIVERS,'fake',Denied)
    p=payload(max_rounds=12)
    bars=market()
    result=run_optimization(p,bars,simulate(bars,ZERO,{},**OPTIONS),OPTIONS)
    assert len(calls)==1
    assert result['optimization']['stop_reason']=='provider_access_denied'
    assert len(result['optimization']['rounds'])==1
    assert result['optimization']['best_round']==0
    assert not result['optimization']['target_reached']


def test_missing_model_secret_rejected_before_enqueue(original_experiment,monkeypatch):
    from app.db import Provider
    database,_,_=original_experiment
    monkeypatch.delenv('MODEL_KEY_NOT_CONFIGURED',raising=False)
    with database.begin() as db:
        model=db.get(Provider,'model')
        model.driver='openai_compatible'
        model.config={'base_url':'https://models.example/v1','model':'gpt','key_env':'MODEL_KEY_NOT_CONFIGURED'}
    with database.begin() as db,pytest.raises(ValueError,match='密钥未配置'):
        enqueue_optimization(db,request(baseline_job_id='baseline'))


def test_keep_rollback_and_next_round_use_incumbent(monkeypatch):
    result, contexts, _, _ = execute(monkeypatch, [
        {'code':LONG, 'params':{'weight':.2}, 'hypothesis':'Trend exposure improves returns'},
        {'code':LONG, 'params':{'weight':.1}, 'hypothesis':'Less exposure improves the score'},
        {'code':LONG, 'params':{'weight':.3}, 'hypothesis':'A small weight increase improves returns'},
    ], max_rounds=3, target_return=1)
    rounds = result['optimization']['rounds']
    assert [row['decision'] for row in rounds] == ['keep', 'rollback', 'keep']
    assert [row['parent_round'] for row in rounds] == [0, 1, 1]
    assert rounds[1]['incumbent_round'] == 1
    assert contexts[2]['strategy']['params'] == {'weight':.2}
    assert contexts[2]['attempts'][-1]['decision'] == 'rollback'
    assert rounds[1]['diagnosis']['delta_metrics']['total_return'] < 0
    assert rounds[1]['next_action'] == 'retry_from_incumbent'
    assert rounds[-1]['next_action'] == 'final_test'
    assert len({row['protocol_hash'] for row in rounds}) == 1


def test_change_budget_and_turnover_are_hard_constraints(monkeypatch):
    result, _, _, _ = execute(monkeypatch, [
        {'code':LONG, 'params':{'a':1, 'b':2, 'c':3, 'weight':.3}},
        {'code':LONG, 'params':{'weight':.3}},
    ], max_rounds=2, target_return=1, max_turnover=.01)
    opt = result['optimization']
    assert opt['rounds'][0]['status'] == 'invalid'
    assert '最小变更' in opt['rounds'][0]['error']
    assert opt['rounds'][1]['decision'] == 'rollback'
    assert not opt['rounds'][1]['eligible']
    assert opt['best_round'] == 0


def test_future_features_are_never_sent_to_model_or_used_in_selection(monkeypatch):
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=30,
                   observed_at='2024-06-10T00:00:00Z', available_at='2024-06-11T00:00:00Z', value=.1)
    left, prompts_left, _, _ = execute(monkeypatch,[{'code':LONG, 'params':{'weight':.3}}],
                                       max_rounds=1, target_return=1, feature_records=[feature])
    right, prompts_right, _, _ = execute(monkeypatch,[{'code':LONG, 'params':{'weight':.3}}],
                                         max_rounds=1, target_return=1, feature_records=[{**feature, 'value':.9}])
    assert prompts_left == prompts_right
    assert all(row['name'] != 'macro.rate' for row in prompts_left[0]['feature_catalog'])
    assert left['optimization']['rounds'] == right['optimization']['rounds']
    assert left['optimization']['feature_hash'] != right['optimization']['feature_hash']


def test_style_is_fixed_in_protocol_and_actual_execution(monkeypatch):
    result, prompts, _, _ = execute(monkeypatch,[{'code':LONG, 'params':{'weight':.3}}],
                                     trading_style='long_trend', target_return=1, max_rounds=1)
    opt = result['optimization']
    assert prompts[0]['trading_style']['rebalance_every'] == 20
    assert opt['evaluation_protocol']['execution']['rebalance_every'] == 20
    assert result['rebalance_every'] == 20
    allowed = {market()[index].date for index in range(1, 200, 20)}
    assert {trade['date'] for trade in result['trades']} <= allowed


def test_feature_strategy_replay_and_provenance(original_experiment, monkeypatch):
    from app.db import Job, Strategy
    from app.runner import run
    from app.services import snapshot
    from app.schemas import RunInput
    database, _, _ = original_experiment
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    code = 'def target(history, params):\n if feature("macro.rate", 0) > 0:\n  return params["weight"]\n return 0'
    result, _, _, p = execute(monkeypatch, [{'code':code, 'params':{'weight':.3}}],
                               feature_records=[feature], trading_style='swing', target_return=.001)
    # Use a new optimization request: changing external data on an inherited baseline is forbidden.
    with database.begin() as db:
        job = enqueue_optimization(db, request(feature_records=[feature], trading_style='swing'))
        result = persist_candidates(db, job, result)
        identifier = result['optimization']['candidate_id']
        candidate = db.get(Strategy, identifier)
        assert candidate.provenance['feature_snapshot']['hash'] == result['feature_snapshot']['hash']
        assert candidate.provenance['rebalance_every'] == 5
        spec = RunInput(strategy_id=identifier, asset_id='asset', mode='demo', start='2024-01-01', end='2024-07-18', client_key='feature-replay')
        replay = snapshot(db, spec)
        baseline = db.get(Job, 'baseline')
        replay['frozen_data'] = baseline.payload['frozen_data']
    replayed = run(replay)
    assert replayed['curve'] == result['curve']
    assert replayed['metrics'] == result['metrics']
    assert replayed['feature_snapshot'] == result['feature_snapshot']


def test_inherited_feature_snapshot_cannot_be_replaced(original_experiment):
    database, _, _ = original_experiment
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    with database.begin() as db, pytest.raises(ValueError, match='沿用原始外部特征'):
        enqueue_optimization(db, request(baseline_job_id='baseline', feature_records=[feature]))


def test_selected_optimization_can_be_manually_approved(original_experiment, monkeypatch):
    from app import main
    from app.db import Strategy
    database, _, _ = original_experiment
    result, _, _, _ = execute(monkeypatch, [{'code':LONG, 'params':{'weight':.3}}])
    with database.begin() as db:
        job = enqueue_optimization(db, request(baseline_job_id='baseline'))
        result = persist_candidates(db, job, result)
        job.result, job.status = result, 'succeeded'
        identifier = result['optimization']['candidate_id']
    monkeypatch.setattr(main, 'Session', database)
    main.approve_strategy(identifier)
    with database() as db:
        assert db.get(Strategy, identifier).status == 'approved'


def test_optimization_keeps_original_warmup_and_phase(monkeypatch):
    from app import optimization
    class NoChange:
        def __init__(self, config):
            pass
        def propose(self, context):
            return {'code':LONG, 'params':{'weight':.3}, 'reason':'minimal change'}
    monkeypatch.setitem(optimization.MODEL_DRIVERS, 'fake', NoChange)
    all_bars = market()
    warmup, bars = all_bars[:25], all_bars[25:]
    p = payload(max_rounds=1, target_return=1, trading_style='swing')
    p['strategy'].update(code=LONG, params={'weight':.3})
    expected = simulate(bars, LONG, {'weight':.3}, warmup_bars=warmup, rebalance_every=5, **OPTIONS)
    result = run_optimization(p, bars, expected, {**OPTIONS, 'warmup_bars':warmup})
    assert result['metrics'] == expected['metrics']
    assert result['curve'] == expected['curve']
    assert result['optimization']['evaluation_protocol']['warmup']['days'] == 25


def test_offline_recipe_is_inherited_by_manual_child_and_cannot_signal(original_experiment, monkeypatch):
    from datetime import datetime, timezone
    from app import main
    from app.db import Strategy
    from app.research_features import freeze_features
    from app.schemas import RunInput, StrategyInput
    from app.services import snapshot
    database, _, _ = original_experiment
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    with database.begin() as db:
        strategy = db.get(Strategy, 'strategy')
        strategy.provenance = {'feature_snapshot':freeze_features([feature]), 'rebalance_every':5}
    monkeypatch.setattr(main, 'Session', database)
    child = main.create_strategy(StrategyInput(name='manual child', code=LONG, params={'weight':.2}, parent_id='strategy'))
    with database.begin() as db:
        candidate = db.get(Strategy, child['id'])
        assert candidate.provenance['rebalance_every'] == 5
        assert candidate.provenance['feature_snapshot']['hash'] == freeze_features([feature])['hash']
        candidate.status = 'approved'
    with database() as db, pytest.raises(ValueError, match='离线回测'):
        snapshot(db, RunInput(kind='signal', strategy_id=child['id'], asset_id='asset', mode='demo',
                              start='2024-01-01', end=datetime.now(timezone.utc).date(), client_key='offline-signal-guard'))


def test_feature_symbol_mismatch_rejected_before_enqueue(original_experiment):
    database, _, _ = original_experiment
    feature = dict(name='fundamental.roe', category='fundamental', symbol='OTHER', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    with database.begin() as db, pytest.raises(ValueError, match='特征标的'):
        enqueue_optimization(db, request(feature_records=[feature]))


def test_tampered_feature_manifest_refuses_replay(original_experiment):
    from app.db import Job
    from app.runner import run
    from app.research_features import freeze_features
    database, _, _ = original_experiment
    with database() as db:
        manifest = deepcopy(db.get(Job, 'baseline').payload)
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    manifest['feature_snapshot'] = freeze_features([feature])
    manifest['feature_snapshot']['records'][0]['value'] = .9
    with pytest.raises(ValueError, match='特征快照指纹'):
        run(manifest)


def test_state_summaries_do_not_repeat_bulk_features(original_experiment, monkeypatch):
    from app import main
    from app.db import Strategy
    from app.research_features import freeze_features
    database, _, _ = original_experiment
    feature = dict(name='macro.rate', category='macro', symbol='*', source='licensed', max_age_days=365,
                   observed_at='2023-12-01T00:00:00Z', available_at='2023-12-02T00:00:00Z', value=.1)
    with database.begin() as db:
        db.get(Strategy, 'strategy').provenance = {'feature_snapshot':freeze_features([feature])}
        job = enqueue_optimization(db, request())
        identifier = job.id
    monkeypatch.setattr(main, 'Session', database)
    state = main.state()
    strategy = next(row for row in state['strategies'] if row['id'] == 'strategy')
    assert strategy['provenance']['feature_snapshot']['record_count'] == 1
    assert 'records' not in strategy['provenance']['feature_snapshot']
    assert all('feature_records' not in row['spec'] for row in state['jobs'])
    assert len(main.get_job(identifier)['payload']['feature_snapshot']['records']) == 1
