import type {Metrics} from './types';
import {pct} from './components';

export interface StrategyStudy {
  study_type?: string;
  selection_rule?: string;
  candidate_count: number;
  evaluation_start: string;
  evaluation_end: string;
  warmup_days: number;
  selected_key: string;
  selected_using_2025_only: string;
  improved_over_baseline: boolean;
  conclusion: string;
  note: string;
  conditions: {initial: number; lot: number; fee_bps: number; slippage_bps: number; max_weight: number};
  rows: {key: string; name: string; eligible: boolean; metrics: Metrics; year_2025: Metrics; year_2026: Metrics; attribution?: {closed_episodes: number}}[];
  lot_sensitivity?: {label: string; initial: number; lot: number; metrics: Metrics}[];
  higher_cost_metrics?: Metrics;
  neighbor_rows?: {key: string; name: string; metrics: Metrics; eligible: boolean}[];
  segments?: {start: string; end: string; metrics?: Metrics}[];
  doubled_cost_metrics: Metrics;
  bootstrap_sharpe: {interval: number[] | null; samples: number; block_days: number; note: string};
  attribution: {execution_count: number; closed_episodes: number; return_excluding_largest_profit: number};
}

export function StrategyStudyReport({study}: {study: StrategyStudy}) {
  const reversal = study.study_type === 'reversal-2026-v1';
  return <section className="research-result" aria-label="有限候选策略对照">
    <h3>{reversal?'2026反转研究（事后选优）':'有限候选对照'} · {study.candidate_count} 个配置</h3>
    <div className="alert">{study.conclusion}</div>
    {study.selection_rule&&<p className="small muted">筛选规则：{study.selection_rule}</p>}
    <p>{study.evaluation_start} → {study.evaluation_end} · 预热 {study.warmup_days} 根日线 · 仓位上限 {pct(study.conditions.max_weight)} · 每手 {study.conditions.lot} 股</p>
    <p className="small muted">佣金 {study.conditions.fee_bps} bps/边，滑点 {study.conditions.slippage_bps} bps/边。{reversal?'18个新增反转配置加原KAMA对照；不是未见数据验证。':'恒定权重基准每日再平衡，不是买入持有。'}所有配置使用相同条件。</p>
    <div className="table-wrap"><table><thead><tr><th>候选</th><th>{reversal?'2026净收益':'累计收益'}</th><th>最大回撤</th><th>夏普</th><th>成交边数</th>{reversal&&<th>完整回合</th>}<th>2025</th><th>2026</th></tr></thead><tbody>{study.rows.map(row=><tr key={row.key}>
      <td><strong>{row.name}</strong>{row.key===study.selected_key&&<small>本次历史收益领先</small>}{!row.eligible&&<small>未满足约束</small>}</td>
      <td>{pct(row.metrics.total_return)}</td><td>{pct(row.metrics.max_drawdown)}</td><td>{row.metrics.sharpe.toFixed(2)}</td><td>{row.metrics.trade_count}</td>{reversal&&<td>{row.attribution?.closed_episodes??'—'}</td>}<td>{pct(row.year_2025.total_return)}</td><td>{pct(row.year_2026.total_return)}</td>
    </tr>)}</tbody></table></div>
    <p>{reversal?'按2025年净收益、回撤与完整回合回顾排序（未加双倍成本门槛）':'只用2025年选优'}：{study.rows.find(row=>row.key===study.selected_using_2025_only)?.name||'无合格配置'}。两年分段各自从现金开始、保留此前预热，不是连续持仓收益拆分，也不是独立样本外检验。</p>
    <p>获选策略：{study.attribution.execution_count} 条成交记录（含再平衡），{study.attribution.closed_episodes} 个已完成持仓段。事后扣除最大单段盈利后收益 {pct(study.attribution.return_excluding_largest_profit)}；不是可交易回测。</p>
    <p>佣金及滑点加倍后累计收益 {pct(study.doubled_cost_metrics.total_return)}。区块Bootstrap夏普95%区间：{study.bootstrap_sharpe.interval?.map(value=>value.toFixed(2)).join(' ～ ')||'不可计算'}（{study.bootstrap_sharpe.samples} 次，区块 {study.bootstrap_sharpe.block_days} 日）。</p>
    {study.lot_sensitivity&&<><h4>交易单位敏感性（不是原条件）</h4><div className="alert">原条件使用每手1股；若改为100股一手，原本金与30%仓位预算可能不足一手。下列结果不改变你的本金、标的或风险设置。</div><div className="table-wrap"><table><thead><tr><th>假设</th><th>本金</th><th>每手股数</th><th>2026净收益</th><th>成交边数</th></tr></thead><tbody>{study.lot_sensitivity.map(row=><tr key={row.label}><td>{row.label}</td><td>{row.initial.toLocaleString()}</td><td>{row.lot}</td><td>{pct(row.metrics.total_return)}</td><td>{row.metrics.trade_count}</td></tr>)}</tbody></table></div></>}
    {study.higher_cost_metrics&&<p>更高成本假设：佣金20 bps/边、滑点10 bps/边后收益 {pct(study.higher_cost_metrics.total_return)}；仍不是完整港股费用模型。</p>}
    {study.neighbor_rows&&<p>参数邻域：{study.neighbor_rows.map(row=>`${row.name}：${pct(row.metrics.total_return)}`).join('；')}。邻近阈值亏损意味着参数敏感。</p>}
    {reversal&&study.segments&&<p>季度现金重启诊断：{study.segments.filter(row=>row.metrics).map(row=>`${row.start}—${row.end}：${pct(row.metrics!.total_return)}`).join('；')}。不是连续持仓收益拆分，不能相加或连乘；四季度只含已有行情。</p>}
    <p className="small muted">{study.bootstrap_sharpe.note}</p><p className="small muted">{study.note}</p>
  </section>;
}
