import {useState, type FormEvent} from 'react';
import {api} from './api';
import {createClientKey} from './clientKey';
import {Badge, money, pct} from './components';
import type {Job, Metrics, Strategy} from './types';
import {StrategyApprovalButton, type ApprovalControls} from './StrategyApprovalButton';

type Props = {job: Job; strategies: Strategy[]; approval: ApprovalControls; busy: boolean; onOpen: (id: string) => void; submitted: (id: string) => void; action: (fn: () => Promise<unknown>, success?: string) => Promise<void>};
const metricRows: {key: keyof Metrics; label: string; percent?: boolean; lower?: boolean}[] = [
  {key:'total_return', label:'累计收益率', percent:true},
  {key:'annual_return', label:'年化收益率', percent:true},
  {key:'max_drawdown', label:'最大回撤', percent:true},
  {key:'sharpe', label:'夏普比率'},
  {key:'volatility', label:'年化波动', percent:true, lower:true},
  {key:'trade_count', label:'成交次数'},
  {key:'turnover', label:'换手率', percent:true},
];
const metricText = (value: number, key: keyof Metrics, percent = false) => percent ? pct(value) : key === 'trade_count' ? String(value) : value.toFixed(2);

export function ExperimentRevision({job, strategies, approval, busy, onOpen, submitted}: Props) {
  const original = job.payload?.strategy;
  const spec = job.payload?.spec;
  const comparison = job.result?.comparison;
  const baselineId = comparison?.baseline_job_id || job.payload?.derivation?.baseline_job_id;
  const current = strategies.find(strategy => strategy.id === original?.id);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState((original?.name.slice(0, 68) || '策略') + ' · 实验调整');
  const [code, setCode] = useState(original?.code || '');
  const [params, setParams] = useState(JSON.stringify(original?.params || {}, null, 2));
  const [notes, setNotes] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [clientKey, setClientKey] = useState(() => createClientKey());
  const complete = job.status === 'succeeded' && ['backtest','research'].includes(job.kind) && !!job.result?.metrics;
  const hasSnapshot = !!(job.payload?.frozen_data?.path || job.result?.snapshot);
  const locked = busy || saving;
  const changed = () => {setClientKey(createClientKey()); setError('');};
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked) return;
    setError('');
    try {
      const parsed: unknown = JSON.parse(params);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(value => typeof value !== 'number' || !Number.isFinite(value))) throw new Error('参数须为键值为有限数值的 JSON 对象');
      if (Object.keys(parsed).length > 30) throw new Error('参数最多30个');
      setSaving(true);
      const result = await api<{id: string}>('/jobs/' + job.id + '/revise-strategy', {name, code, params: parsed, notes, client_key: clientKey});
      setEditing(false);
      submitted(result.id);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setSaving(false);
    }
  }
  if (!complete && !baselineId) return null;
  return <section className="experiment-revision">
    <div className="section-row"><div><h3>基于历史实验调整策略</h3><p className="small muted">{original?.name || '原策略'} · 版本 {original?.id.slice(0,8) || '—'} {current && <Badge value={current.status}/>}</p></div>
      <div className="row">{baselineId && <button type="button" disabled={locked} onClick={() => onOpen(baselineId)}>查看来源实验</button>}
        {complete && <button type="button" className="primary" disabled={locked || !original || !hasSnapshot} onClick={() => {setEditing(!editing); setError('');}}>{editing ? '收起调整' : '基于此实验调整策略'}</button>}
      </div>
    </div>
    {complete && (!original || !hasSnapshot) && <p className="small muted">原实验缺少策略或行情快照，不能进行同条件复测。</p>}
    {job.kind === 'research' && <p className="small muted">调整对象为报告主曲线对应的原策略，并非研究候选版本。</p>}
    {comparison && <div className="experiment-comparison">
      <div className="table-wrap"><table><thead><tr><th>对比指标</th><th>调整前 · {comparison.baseline_strategy_name}</th><th>调整后</th><th>变化</th></tr></thead><tbody>
        {metricRows.map(row => {
          const before = comparison.baseline_metrics[row.key], after = comparison.candidate_metrics[row.key], delta = after - before;
          const neutral = ['trade_count','turnover'].includes(row.key);
          const tone = neutral || delta === 0 ? '' : (row.lower ? delta < 0 : delta > 0) ? 'positive' : 'negative';
          return <tr key={row.key}><td>{row.label}</td><td>{metricText(before, row.key, row.percent)}</td><td>{metricText(after, row.key, row.percent)}</td><td className={tone}>{delta > 0 ? '+' : ''}{row.percent ? (delta * 100).toFixed(2) + ' 个百分点' : metricText(delta, row.key)}</td></tr>;
        })}
      </tbody></table></div>
      <p className="small muted">{comparison.note}</p>
      {!comparison.baseline_reproduced && <div className="alert">原报告指标与当前引擎重放不完全相同，上表以同引擎重放为基线；原报告保持不变。</div>}
      <details><summary>对比实验条件与原记录指标</summary><pre>{JSON.stringify({conditions:comparison.conditions, original_metrics:comparison.original_metrics}, null, 2)}</pre></details>
      {current && <div className="row"><Badge value={current.status}/><StrategyApprovalButton strategyId={current.id} status={current.status} busy={locked} label="人工批准此版本" {...approval}/><span className="small muted">批准不会自动替换运行策略或自动下单。</span></div>}
    </div>}
    {editing && original && spec && <form className="revision-form" onSubmit={submit}>
      <div className="alert">复用原实验冻结行情，不重新抓取数据；保留初始资金、交易单位、费用、滑点及仓位上限。仅保存新版本并提交后台复测，不批准、不下单。</div>
      <div className="revision-conditions"><span>区间 <b>{String(spec.start)} — {String(spec.end)}</b></span><span>初始资金 <b>{money(Number(spec.initial))} {job.result?.currency}</b></span><span>费用 / 滑点 <b>{String(spec.fee_bps)} / {String(spec.slippage_bps)} bps</b></span><span>仓位上限 <b>{pct(job.payload?.risk?.max_weight)}</b></span></div>
      <div className="form-grid"><label>新版本名称<input required value={name} maxLength={80} disabled={locked} onChange={event => {setName(event.target.value); changed();}}/></label><label>变更说明<textarea value={notes} maxLength={2000} rows={2} disabled={locked} placeholder="结合收益、回撤与交易明细说明调整原因" onChange={event => {setNotes(event.target.value); changed();}}/></label></div>
      <div className="form-grid"><label>原参数（只读）<pre className="revision-params mono">{JSON.stringify(original.params,null,2)}</pre></label><label>新参数 JSON<textarea className="revision-params mono" value={params} required rows={5} disabled={locked} onChange={event => {setParams(event.target.value); changed();}}/><small>填写完整参数对象，未填写的参数不会自动继承。</small></label></div>
      <div className="code-comparison"><label>实验原代码（只读）<pre className="code-editor original">{original.code}</pre></label><label>调整后代码<textarea className="code-editor" value={code} required maxLength={12000} rows={14} spellCheck={false} disabled={locked} onChange={event => {setCode(event.target.value); changed();}}/></label></div>
      <p className="small muted">反复使用同一区间调参会造成过拟合；复测提升不代表未来收益，发布前应在未用于调整的区间验证。返回目标仓位范围 0–1，仍受原实验仓位上限约束。</p>
      {error && <div className="alert danger" role="alert">{error}</div>}
      <footer><button type="button" disabled={locked} onClick={() => setEditing(false)}>取消</button><button className="primary" disabled={locked}>{saving ? '提交中…' : '保存新版本并同条件复测'}</button></footer>
    </form>}
  </section>;
}
