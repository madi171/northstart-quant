import {useEffect, useState, type FormEvent} from 'react';
import {api} from './api';
import {createClientKey} from './clientKey';
import {researchDataMode} from './dataMode';
import {Badge, Empty, time} from './components';
import type {Job, State} from './types';
import './optimization.css';

type Props = {state: State; mode: string; onModeChange: (mode: string) => void; busy: boolean; onOpen: (id: string) => void; submitted: (id: string) => void};
type Conditions = {strategy_id: string; asset_id: string; mode: string; start: string; end: string; initial: number; fee_bps: number; slippage_bps: number};
const date = () => new Date().toISOString().slice(0, 10);
const defaults = (state: State, mode: string): Conditions => ({strategy_id: state.strategies[0]?.id || '', asset_id: state.assets.find(asset => asset.enabled)?.id || '', mode: researchDataMode(mode), start: new Date(Date.now() - 3 * 365 * 86400000).toISOString().slice(0, 10), end: date(), initial: 100000, fee_bps: 5, slippage_bps: 5});

export function OptimizationWorkbench({state, mode, onModeChange, busy, onOpen, submitted}: Props) {
  const [conditions, setConditions] = useState(() => defaults(state, mode));
  const [baselineId, setBaselineId] = useState('');
  const [baseline, setBaseline] = useState<Job | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [metric, setMetric] = useState('total_return');
  const [target, setTarget] = useState(5);
  const [rounds, setRounds] = useState(5);
  const [tradingStyle, setTradingStyle] = useState('balanced');
  const [hypothesis, setHypothesis] = useState('');
  const [featureText, setFeatureText] = useState('');
  const [drawdown, setDrawdown] = useState(15);
  const [minTrades, setMinTrades] = useState(1);
  const [maxTurnover, setMaxTurnover] = useState(50);
  const [maxChangedLines, setMaxChangedLines] = useState(12);
  const [maxParamChanges, setMaxParamChanges] = useState(3);
  const [confirmed, setConfirmed] = useState(false);
  const [clientKey, setClientKey] = useState(() => createClientKey());
  const locked = busy || loading || saving;
  const providers = state.providers.filter(provider => provider.enabled && provider.kind === 'model' && (conditions.mode === 'demo' ? provider.driver === 'demo_model' : provider.driver === 'openai_compatible'));
  const selectedModels = models.filter(id => providers.some(provider => provider.id === id));
  const baselines = state.jobs.filter(job => job.status === 'succeeded' && ['backtest', 'research'].includes(job.kind));
  const recent = state.jobs.filter(job => job.kind === 'optimization').slice(0, 5);
  const changed = () => {setClientKey(createClientKey()); setConfirmed(false); setError('');};
  useEffect(() => {
    if (baselineId) return;
    setConditions(previous => ({...previous, strategy_id: previous.strategy_id || state.strategies[0]?.id || '', asset_id: previous.asset_id || state.assets.find(asset => asset.enabled)?.id || ''}));
  }, [baselineId, state.strategies, state.assets]);
  useEffect(() => {
    if (baselineId) return;
    setConditions(previous => ({...previous, mode: researchDataMode(mode)}));
    setModels([]); setConfirmed(false); setClientKey(createClientKey());
  }, [baselineId, mode]);
  useEffect(() => {
    if (!baselineId) {setBaseline(null); setLoading(false); return;}
    let active = true;
    setBaseline(null); setLoading(true); setError('');
    api<Job>('/jobs/' + baselineId).then(job => {
      if (!active) return;
      const spec = job.payload?.spec || job.spec;
      const next: Conditions = {strategy_id: String(spec.strategy_id || job.payload?.strategy?.id || ''), asset_id: String(spec.asset_id || job.payload?.asset?.id || ''), mode: String(spec.mode || job.result?.data_mode || ''), start: String(spec.start || ''), end: String(spec.end || ''), initial: Number(spec.initial), fee_bps: Number(spec.fee_bps), slippage_bps: Number(spec.slippage_bps)};
      if (!['demo', 'historical'].includes(next.mode) || !next.strategy_id || !next.asset_id || !next.start || !next.end || ![next.initial, next.fee_bps, next.slippage_bps].every(Number.isFinite)) throw new Error('原实验条件不完整或不是演示 / 历史回放实验，无法继承。');
      setConditions(next); setBaseline(job);
    }).catch(failure => {if (active) setError((failure as Error).message);}).finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [baselineId]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked) return;
    if (!confirmed || !selectedModels.length || selectedModels.length > 2 || (baselineId && !baseline)) {setError('请检查实验、选择1–2个适用模型，并确认调用预算。'); return;}
    if (!conditions.strategy_id || !conditions.asset_id || conditions.start >= conditions.end || conditions.end > date()) {setError('请选择策略、标的及有效的历史日期区间。'); return;}
    setSaving(true); setError('');
    try {
      const records: unknown = baselineId || !featureText.trim() ? [] : JSON.parse(featureText);
      if (!Array.isArray(records) || records.length > 5000 || records.some(row => !row || typeof row !== 'object' || Array.isArray(row))) throw new Error('特征文件须为JSON对象数组，最多5000条；具体字段由服务端校验。');
      const job = await api<{id: string}>('/optimizations', {...conditions, kind: 'optimization', model_ids: selectedModels, client_key: clientKey, target_metric: metric, target_return: target / 100, max_rounds: rounds, max_drawdown: drawdown / 100, min_trades: minTrades, max_turnover: maxTurnover, trading_style: tradingStyle, hypothesis, max_changed_lines: maxChangedLines, max_param_changes: maxParamChanges, feature_records: records, max_output_tokens: 6000, time_budget_seconds: 900, ...(baselineId ? {baseline_job_id: baselineId} : {})});
      setClientKey(createClientKey()); setConfirmed(false); submitted(job.id);
    } catch (failure) {setError((failure as Error).message);} finally {setSaving(false);}
  }
  function update<K extends keyof Conditions>(key: K, value: Conditions[K]) {setConditions(previous => ({...previous, [key]: value})); changed();}
  return <section className="panel optimization-workbench">
    <div className="panel-heading"><div><h3>策略自主研究</h3><p className="small muted">提出假设 → 最小修改 → 固定评估 → 诊断 → 保留或回滚 → 记录实验 → 决定下一轮。最终测试不参与调参。</p></div><span className="tag">AUTO RESEARCH</span></div>
    <form className="form" onSubmit={submit}>
      <label>起始实验<select value={baselineId} disabled={locked} onChange={event => {setBaselineId(event.target.value); changed(); if (!event.target.value) setConditions(defaults(state, mode));}}><option value="">从当前策略创建新实验</option>{baselines.map(job => <option key={job.id} value={job.id}>{job.summary?.symbol || job.id.slice(0, 8)} · {time(job.created_at)}</option>)}</select><small>选择原实验后继承其策略、标的及测试条件，不能修改。</small></label>
      {loading && <p role="status">正在读取原实验冻结条件…</p>}
      <fieldset disabled={locked || !!baselineId} className="optimization-conditions">
        <div className="form-grid"><label>策略版本<select required value={conditions.strategy_id} onChange={event => update('strategy_id', event.target.value)}>{!state.strategies.some(item => item.id === conditions.strategy_id) && <option value={conditions.strategy_id}>{baseline?.payload?.strategy?.name || conditions.strategy_id || '请选择策略'}</option>}{state.strategies.map(strategy => <option key={strategy.id} value={strategy.id}>{strategy.name} · {strategy.id.slice(0, 6)}</option>)}</select></label><label>投资标的<select required value={conditions.asset_id} onChange={event => update('asset_id', event.target.value)}>{!state.assets.some(item => item.id === conditions.asset_id && item.enabled) && <option value={conditions.asset_id}>{baseline?.payload?.asset?.symbol || conditions.asset_id || '请选择标的'}</option>}{state.assets.filter(asset => asset.enabled).map(asset => <option key={asset.id} value={asset.id}>{asset.name} / {asset.symbol}</option>)}</select></label></div>
        <div className="form-grid three"><label>数据环境<select value={conditions.mode} onChange={event => {update('mode', event.target.value); onModeChange(event.target.value);}}><option value="historical">历史回放 / 真实模型</option><option value="demo">演示数据 / 演示模型</option></select><small>{baselineId ? '沿用原实验环境，不影响默认偏好。' : mode === 'live' ? '自主研究不支持在线数据，新实验使用历史回放。' : '自动记住此浏览器的选择，与工作台总览共用。'}</small></label><label>开始日期<input type="date" required min="2015-01-01" value={conditions.start} onChange={event => update('start', event.target.value)}/></label><label>结束日期<input type="date" required max={date()} value={conditions.end} onChange={event => update('end', event.target.value)}/></label></div>
        <div className="form-grid three"><label>初始资金<input type="number" required min={1} max={1e10} value={conditions.initial} onChange={event => update('initial', Number(event.target.value))}/></label><label>费用 / bps<input type="number" required min={0} max={500} step="0.1" value={conditions.fee_bps} onChange={event => update('fee_bps', Number(event.target.value))}/></label><label>滑点 / bps<input type="number" required min={0} max={500} step="0.1" value={conditions.slippage_bps} onChange={event => update('slippage_bps', Number(event.target.value))}/></label></div>
      </fieldset>
      <div className="form-grid"><label>交易风格<select disabled={locked} value={tradingStyle} onChange={event => {setTradingStyle(event.target.value); changed();}}><option value="balanced">均衡日线 · 每日可调仓</option><option value="long_trend">长线趋势 · 每20个交易日可调仓</option><option value="swing">短线波段 · 每5个交易日可调仓</option></select><small>调仓节奏作为固定执行规则；长线建议60–250日趋势，波段建议5–30日信号。当前均为日线，持仓周期由信号决定。</small></label><label>首轮研究假设（可选）<textarea disabled={locked} maxLength={1000} value={hypothesis} placeholder="例如：增加成交量过滤可减少横盘期间的趋势假突破。" onChange={event => {setHypothesis(event.target.value); changed();}}/></label></div>
      <details><summary>多源研究特征 · 财报 / 宏观 / 盘口 / 公告 / 其他</summary><p className="small muted">内置价格与成交量可直接使用。外部输入目前通过JSON快照导入，须自行提供已获授权的数据；尚无自动财报或盘口订阅。公告、新闻需先转换为数值因子。历史环境中的因子预览会发送给所选模型。</p><label>导入特征JSON<input type="file" accept=".json,application/json" disabled={locked || !!baselineId} onChange={async event => {const file = event.target.files?.[0]; if (!file) return; changed(); if (file.size > 2_000_000) {setError('特征文件最多2MB。'); return;} try {setFeatureText(await file.text());} catch {setError('无法读取特征文件。');}}}/></label><label>特征记录<textarea disabled={locked || !!baselineId} rows={8} value={featureText} onChange={event => {setFeatureText(event.target.value); changed();}} placeholder={'[{"name":"fundamental.roe","category":"fundamental","symbol":"US.AAPL","observed_at":"2024-03-31T00:00:00Z","available_at":"2024-05-01T12:00:00Z","value":0.15,"source":"licensed-financials","max_age_days":180}]'}/></label><small>{baselineId ? '沿用原实验冻结的外部特征，不能在同一基准比较中更换。' : '最多5000条、64个字段。宏观全市场因子用 symbol="*"；时间必须含时区。available_at 应取首次发布与首次实际获取中较晚者；修订另存版本，不回填历史。仅在之后的UTC日期可用，过期视为缺失。策略通过 feature("字段名", 缺失默认值) 读取。盘口为日级衍生因子，无法模拟逐笔成交。'}</small></details>
      <div className="form-grid three"><label>验证集目标<select aria-describedby="validation-objective-hint" disabled={locked} value={metric} onChange={event => {setMetric(event.target.value); changed();}}><option value="total_return">累计收益率</option><option value="annual_return">年化收益率</option></select></label><label>目标收益率 / %<input aria-describedby="validation-objective-hint" disabled={locked} type="number" required min={0.1} max={500} step="0.1" value={target} onChange={event => {setTarget(Number(event.target.value)); changed();}}/></label><label>最大轮数<input disabled={locked} type="number" required min={1} max={12} step={1} value={rounds} onChange={event => {setRounds(Number(event.target.value)); changed();}}/></label></div>
      <div className="validation-objective-help">
        <p id="validation-objective-hint" aria-live="polite">{metric === 'total_return' ? `当前目标：验证区间累计收益至少 ${target}%。衡量这段区间实际赚了多少，不代表每年都能获得该收益。` : `当前目标：验证区间收益按复利折算后，年化至少 ${target}%。不是要求这段区间实际赚到 ${target}%；短区间会放大年化数值。`}</p>
        <details>
          <summary>累计收益率与年化收益率，应该怎么选？</summary>
          <dl>
            <dt>累计收益率：这段验证区间赚了多少</dt>
            <dd>计算为「期末账户权益 ÷ 初始资金 − 1」。例如10万元变成10.5万元，累计收益为5%，无论这段区间长短。适合关心固定区间实际表现、比较相同区间策略；不同长度区间不能只看累计收益高低。</dd>
            <dt>年化收益率：按同样复利速度折算一年是多少</dt>
            <dd>采用252交易日年化，公式为「(1 + 累计收益率) 的 (252 ÷ N) 次方 − 1」，N为报告引擎采用的日收益期数，不是成交笔数或持仓天数，也不是简单的算术平均。便于统一时间尺度，但不能消除市场阶段、风险和样本长度的差异。</dd>
            <dt>同样累计赚5%，年化可能差很多</dt>
            <dd>按上述口径，N为126时，累计5%折算年化约10.25%；N为252时为5%；N为504时约2.47%。反过来，N为126时，年化目标5%只对应约2.47%的累计收益。以上是复利换算示例，不是未来收益预测。</dd>
            <dt>如何选择与设置目标</dt>
            <dd>第一次研究或验证区间较短时，建议先选累计收益率，更直观；需要年化口径且有足够长、覆盖不同市场阶段的数据时，可选年化收益率。同一固定区间内，两者随期末权益单调变化，通常不会改变收益排序；但同样填写5%，达标门槛不同，可能改变停止轮次。切换指标不会自动换算输入值，请重新核对目标。</dd>
            <dt>这里看的是验证集，不是全区间</dt>
            <dd>行情按时间分为60%训练、20%调优验证、20%最终测试。这里的目标针对中间20%的验证区间，不是整个所选日期区间；例如选择3年行情，验证区间通常仅约占其中20%，并不等于验证了3年。区间按实际行情条数划分。</dd>
            <dt>收益达标不等于策略可靠</dt>
            <dd>收益以整个账户权益计算，计入配置的费用、滑点及期末持仓收盘估值，不只是已平仓利润；当前模拟并不包含所有真实成本。两种指标都不能单独反映风险，仍需同时检查回撤、成交笔数、换手及最终测试表现。短样本、少量成交或单次大涨可能产生很高的年化；达到目标还须满足风险与成交约束。反复查看同一最终测试区间后再调参，会失去独立性，任何目标都不是收益保证。</dd>
          </dl>
        </details>
      </div>
      <div className="form-grid three"><label>最大回撤 / %<input disabled={locked} type="number" required min={0.1} max={100} step="0.1" value={drawdown} onChange={event => {setDrawdown(Number(event.target.value)); changed();}}/></label><label>至少成交笔数<input disabled={locked} type="number" required min={1} max={1000} value={minTrades} onChange={event => {setMinTrades(Number(event.target.value)); changed();}}/></label><label>最大换手 / 初始资金倍数<input disabled={locked} type="number" required min={0.01} max={1000} step="0.01" value={maxTurnover} onChange={event => {setMaxTurnover(Number(event.target.value)); changed();}}/></label></div>
      <div className="form-grid"><label>每轮最多修改代码行<input disabled={locked} type="number" required min={1} max={80} value={maxChangedLines} onChange={event => {setMaxChangedLines(Number(event.target.value)); changed();}}/></label><label>每轮最多修改参数数<input disabled={locked} type="number" required min={1} max={10} value={maxParamChanges} onChange={event => {setMaxParamChanges(Number(event.target.value)); changed();}}/></label></div>
      <fieldset disabled={locked}><legend>协作模型 · 选择1–2个</legend>{providers.length ? providers.map(provider => <label className="checkbox" key={provider.id}><input type="checkbox" checked={selectedModels.includes(provider.id)} disabled={!selectedModels.includes(provider.id) && selectedModels.length >= 2} onChange={event => {setModels(event.target.checked ? [...selectedModels, provider.id] : selectedModels.filter(id => id !== provider.id)); changed();}}/>{provider.name}</label>) : <p className="small muted">没有适用模型，请先在模型协作中启用{conditions.mode === 'demo' ? '演示模型 demo_model' : '真实模型 openai_compatible'}。</p>}</fieldset>
      <div className="alert">费用、滑点、仓位上限、风格调仓节奏及约束在本次实验内固定；不合规或未严格改善的候选回滚，所有轮次保留记录。单次输出上限6000 tokens，总时限900秒。反复使用验证集仍有过拟合风险。</div>
      <label className="checkbox"><input type="checkbox" disabled={locked} checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>{conditions.mode === 'historical' ? '我确认会调用外部模型并可能产生费用，接受上述轮数、输出与时间预算。' : '我确认这是演示优化，不会调用真实外部模型，结果不代表真实收益。'}</label>
      {error && <div className="alert danger" role="alert">{error}</div>}
      <footer><button className="primary" disabled={locked || !confirmed || !selectedModels.length || !conditions.strategy_id || !conditions.asset_id || (!!baselineId && !baseline)}>{saving ? '提交中…' : '提交策略优化'}</button></footer>
    </form>
    <div className="optimization-recent"><h3>最近优化任务</h3>{recent.length ? recent.map(job => <div className="optimization-job" key={job.id}><div><strong>{job.summary?.symbol || job.id.slice(0, 8)}</strong><small>{time(job.created_at)}</small></div><Badge value={job.status}/><button type="button" onClick={() => onOpen(job.id)}>查看优化报告</button></div>) : <Empty>尚无优化任务</Empty>}</div>
  </section>;
}
