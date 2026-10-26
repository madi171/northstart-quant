import {api, download} from './api';
import {StrategyApprovalButton, type ApprovalControls} from './StrategyApprovalButton';
import {Badge, Empty, pct} from './components';
import type {Candidate, Job, Metrics, SignalDiagnostics, Strategy} from './types';
import {researchManifest} from './researchManifest';
import {NamingSource, StrategyDiff, StrategyExplanation} from './ResearchEvidence';
import type {ResearchExplanation} from './ResearchEvidence';
import './optimization.css';

export interface OptimizationRound extends ResearchExplanation {
  round: number;
  status?: string;
  provider_name?: string;
  error?: string;
  validation_metrics?: Partial<Metrics> | null;
  training_metrics?: Partial<Metrics> | null;
  notes?: string;
  usage?: Record<string, unknown> | null;
  eligible?: boolean;
  candidate_id?: string;
  code?: string;
  params?: Record<string, number>;
  hypothesis?: string;
  expected_effect?: string;
  parent_round?: number;
  incumbent_round?: number;
  decision?: 'keep' | 'rollback';
  decision_reason?: string;
  next_action?: string;
  change?: {changed_lines: number; param_changes: Record<string, {before: number | null; after: number | null}>; diff: string};
  diagnosis?: {summary: string; next_hypothesis: string; delta_metrics?: Partial<Metrics>};
}
export interface OptimizationResult {
  baseline?: {training_metrics?: Partial<Metrics>; validation_metrics?: Partial<Metrics>};
  selected?: Partial<Candidate> | null;
  test_signal_diagnostics?: SignalDiagnostics | null;
  duration_seconds?: number;
  phase?: string;
  rounds?: OptimizationRound[];
  best_round?: number | null;
  best_validation_metrics?: Partial<Metrics> | null;
  target_reached?: boolean;
  stop_reason?: string;
  max_rounds?: number;
  target_return?: number;
  target_metric?: string;
  split?: {train_start?: string; train_end?: string; validation_start?: string; validation_end?: string; test_start?: string; test_end?: string; days?: number | Record<string, number>; [key: string]: unknown};
  test_metrics?: Partial<Metrics> | null;
  test_baseline_metrics?: Partial<Metrics> | null;
  test_passed?: boolean;
  test_eligible?: boolean;
  candidate_id?: string | null;
  full_period_note?: string;
  note?: string;
  style?: {label: string; rebalance_every: number; guidance: string};
  evaluation_protocol?: Record<string, unknown>;
  protocol_hash?: string;
  feature_hash?: string;
  feature_catalog?: {name: string; category: string; source: string}[];
}
type Props = {job: Job; strategies: Strategy[]; approval: ApprovalControls; busy: boolean; action: (fn: () => Promise<unknown>, success?: string) => Promise<void>};
const phases: Record<string, string> = {baseline: '建立基线', training: '训练生成', validation: '验证评估', optimizing: '迭代优化', test: '最终测试', completed: '已结束', queued: '排队中', running: '运行中'};
const stopReasons: Record<string, string> = {provider_access_denied: '模型访问受限或限流，已停止调用，请核验网关权限/密钥/网络', running: '正在迭代', baseline_target_reached: '原策略已满足验证目标，无需继续迭代', target_reached: '候选已达到验证目标', time_budget: '已达到运行时间预算', max_rounds: '已达到最大轮数', cancelled: '已人工取消'};
const metricName = (key?: string) => key === 'annual_return' ? '年化收益率' : '累计收益率';
function metricRow(name: string, metrics?: Partial<Metrics> | null) {
  return <tr><td>{name}</td><td>{pct(metrics?.total_return)}</td><td>{pct(metrics?.annual_return)}</td><td>{pct(metrics?.max_drawdown)}</td><td>{metrics?.sharpe?.toFixed(2) ?? '—'}</td><td>{metrics?.trade_count ?? '—'}</td></tr>;
}

function ResearchDecision({round}: {round: OptimizationRound}) {
  const actions: Record<string, string> = {stop_provider: '停止调用并检查模型服务', final_test: '结束搜索，执行最终测试', refine_kept: '从本轮保留版本继续最小修改', retry_from_incumbent: '从此前保留版本重新提出假设'};
  return <div className="research-decision">
    <StrategyExplanation strategy={round}/>
    {(round.hypothesis || round.expected_effect) && <div className="research-hypothesis">{round.hypothesis && <p><strong>待验证假设：</strong>{round.hypothesis}</p>}{round.expected_effect && <p><strong>预期效果（非实测）：</strong>{round.expected_effect}</p>}</div>}
    <StrategyDiff change={round.change} parentLabel={round.parent_round === undefined ? '记录中的父版本' : round.parent_round === 0 ? '原策略（第 0 轮）' : `第 ${round.parent_round} 轮保留版本`}/>
    <div className="research-verdict">
      <h4>评估结论</h4>
      {round.decision && <p><span className={'tag ' + (round.decision === 'keep' ? 'green' : '')}>{round.decision === 'keep' ? '保留' : '回滚'}</span> {round.decision_reason}{round.incumbent_round !== undefined && ` · 当前保留第 ${round.incumbent_round} 轮`}</p>}
      {round.diagnosis && <><p><strong>实测诊断：</strong>{round.diagnosis.summary}</p>{round.diagnosis.delta_metrics && <p className="small muted">相对本轮父版本：验证累计收益变化 {pct(round.diagnosis.delta_metrics.total_return)} · 夏普变化 {round.diagnosis.delta_metrics.sharpe?.toFixed(2) ?? '—'} · 换手变化 {round.diagnosis.delta_metrics.turnover?.toFixed(2) ?? '—'}</p>}<p className="small muted">下一假设方向：{round.diagnosis.next_hypothesis}</p></>}
      {round.next_action && <p><strong>下一步：</strong>{actions[round.next_action] || round.next_action}</p>}
    </div>
  </div>;
}

export function OptimizationReport({job, strategies, approval, busy, action}: Props) {
  const result = (job.result as (NonNullable<Job['result']> & {optimization?: OptimizationResult}) | undefined)?.optimization;
  const running = ['queued', 'running'].includes(job.status);
  const locked = busy || !!approval.pending;
  const manifest = researchManifest(job);
  return <section className="optimization-report">
    <div className="section-row"><div><h3>策略自主迭代报告</h3><p className="small muted">训练与调优验证用于反复反馈，最终测试保持独立；只读研究证据，不自动发布或交易。</p></div>{running && <button type="button" disabled={locked} onClick={() => action(() => api('/jobs/' + job.id + '/cancel', {}), '已请求取消优化任务')}>取消优化任务</button>}</div>
    <p className="small muted">运行预算：最多 {String(job.spec?.max_rounds ?? result?.max_rounds ?? 5)} 轮 · 单次输出上限 {String(job.spec?.max_output_tokens ?? 6000)} tokens · 时限 {String(job.spec?.time_budget_seconds ?? 900)} 秒{result?.duration_seconds !== undefined && ` · 已耗时 ${result.duration_seconds.toFixed(1)} 秒`}</p>
    {!result ? <Empty>{running ? '后台正在准备优化实验，状态更新后将显示迭代记录。' : '此任务暂无优化结果。'}</Empty> : <>
      <div className="optimization-summary"><div><span>验证集目标 · {metricName(result.target_metric)}</span><strong>{pct(result.target_return)}</strong></div><div><span>当前阶段</span><strong>{phases[result.phase || ''] || result.phase || job.status}</strong></div><div><span>最佳轮次 / 最大轮数</span><strong>{result.best_round ?? '—'} / {result.max_rounds ?? '—'}</strong></div><div><span>目标达成</span><strong>{result.target_reached === undefined ? '待评估' : result.target_reached ? '验证集已达标' : '未达标'}</strong></div></div>
      {result.selected && <section className="research-selected" aria-label="选定策略摘要"><div className="section-row"><div><p className="small muted">{running ? '当前选定策略 · 仍在评估' : '最终选定策略'} · 第 {result.selected.round ?? result.best_round ?? 0} 轮{(result.selected.round ?? result.best_round) === 0 && ' · 保留原策略'}</p><h3>{result.selected.name || '名称未记录'}</h3></div><NamingSource source={result.selected.naming_source}/></div>{(result.selected.round ?? result.best_round) === 0 ? <p className="small muted">未选出需要替换的迭代版本，沿用原策略名称，不额外调用模型重命名。</p> : <StrategyExplanation strategy={result.selected}/>}</section>}
      {result.stop_reason && <div className="alert">停止原因：{stopReasons[result.stop_reason] || result.stop_reason}</div>}
      {result.note && <p className="small muted">{result.note}</p>}
      <section className="optimization-section"><div className="section-row"><h3>固定实验协议{result.style && ` · ${result.style.label}`}</h3><div className="research-exports"><button type="button" onClick={() => download(`research-${job.id}.json`, {format:'northstar-autoresearch-v1', job_id:job.id, status:job.status, optimization:result, feature_snapshot:job.result?.feature_snapshot})}>导出实验日志</button><button type="button" disabled={!manifest} onClick={() => manifest && download(`replay-${job.id}.json`, manifest)}>导出最终策略复现清单</button></div></div><p className="small muted">{result.style ? `每 ${result.style.rebalance_every} 个交易日允许调仓；` : ''}保留须满足固定风险约束且验证成绩严格改善。回滚候选仍保留研究记录，下一轮从当前保留版本开始。</p>{result.protocol_hash && <p className="small muted">协议指纹 <code>{result.protocol_hash}</code></p>}{result.feature_hash && <p className="small muted">特征快照指纹 <code>{result.feature_hash}</code></p>}{result.feature_catalog && <details><summary>可用数据与覆盖情况 · {result.feature_catalog.length} 个字段</summary><p className="small muted">内置OHLCV在当日收盘后可知；外部特征严格早于交易日UTC零点才可读取，过期与缺失使用策略显式默认值。覆盖天数为最终策略全区间统计。</p><div className="table-wrap"><table><thead><tr><th>字段</th><th>类别</th><th>来源</th><th>有效覆盖天数</th></tr></thead><tbody>{result.feature_catalog.map(feature => <tr key={feature.name}><td><code>{feature.name}</code></td><td>{feature.category}</td><td>{feature.source}</td><td>{feature.source === 'frozen OHLCV' ? job.result?.curve.length ?? '—' : job.result?.feature_coverage?.[feature.name] ?? '—'}</td></tr>)}</tbody></table></div></details>}{result.evaluation_protocol && <details><summary>查看固定执行与选优规则</summary><pre>{JSON.stringify(result.evaluation_protocol, null, 2)}</pre></details>}<p className="small muted">复现命令：<code>PYTHONPATH=backend python -m app.cli --manifest replay-任务ID.json --out data/replay.json</code>。需要同一 DATA_DIR 行情快照；复现清单不会再次调用模型。</p></section>
      {result.split && <section className="optimization-section"><h3>数据划分 · 最终测试独立</h3><div className="optimization-splits">{[['训练集', result.split.train_start, result.split.train_end], ['调优验证集', result.split.validation_start, result.split.validation_end], ['最终测试集', result.split.test_start, result.split.test_end]].map(([label, start, end]) => <div key={label}><strong>{label}</strong><span>{start || '—'} — {end || '—'}</span></div>)}</div><p className="small muted">交易日数：训练 {String(result.split.train_days ?? '—')} · 调优验证 {String(result.split.validation_days ?? '—')} · 最终测试 {String(result.split.test_days ?? '—')}</p>{result.split.days !== undefined && <p className="small muted">交易日数：{typeof result.split.days === 'number' ? result.split.days : JSON.stringify(result.split.days)}</p>}<p className="small muted">调优验证集参与反复反馈；最终测试数据不参与模型调参，验证集达标不等于最终测试通过。</p></section>}
      <section className="optimization-section"><h3>评估指标</h3><div className="table-wrap"><table><thead><tr><th>评估区间 / 版本</th><th>累计收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>成交笔数</th></tr></thead><tbody>{result.baseline && metricRow('原策略 · 训练集', result.baseline.training_metrics)}{result.baseline && metricRow('原策略 · 验证集', result.baseline.validation_metrics)}{metricRow('最佳候选 · 验证集', result.best_validation_metrics)}{metricRow('原策略 · 最终测试', result.test_baseline_metrics)}{metricRow('最佳候选 · 最终测试', result.test_metrics)}</tbody></table></div><p className="small muted">最终测试约束：{result.test_eligible === undefined ? '待评估' : result.test_eligible ? '满足风险 / 成交约束' : '未满足约束'}；收益目标：{result.test_passed === undefined ? '待评估' : result.test_passed ? '通过' : '未通过'}。不同数据区间的收益不能直接作为同条件提升比较。</p></section>
      <section className="optimization-section research-timeline"><h3>逐轮研究记录 <span className="subtle">{result.rounds?.length || 0} 轮</span></h3><p className="small muted">每轮对比实际父版本，而非简单对比上一轮；回滚尝试不会进入保留版本链。</p>{result.rounds?.length ? result.rounds.map((round, index) => <details className="optimization-round" key={String(round.round) + '-' + index} open={index === result.rounds!.length - 1 || round.round === result.best_round}>
        <summary><span className="research-round-heading"><strong>第 {round.round} 轮 · {round.name || '候选名称未记录'}</strong><small>{round.provider_name || '等待模型'}{round.parent_round !== undefined && ` · 基于第 ${round.parent_round} 轮`}{round.change && ` · ${round.change.changed_lines} 行代码 / ${Object.keys(round.change.param_changes).length} 个参数`}</small></span><span className="research-round-status">{round.status && <Badge value={round.status}/>}<span className={'tag ' + (round.decision === 'keep' ? 'green' : '')}>{round.decision === 'keep' ? '保留' : round.decision === 'rollback' ? '回滚' : '决策未记录'}</span>{round.round === result.best_round && <span className="tag green">最佳验证候选</span>}</span></summary>
        <NamingSource source={round.naming_source}/><ResearchDecision round={round}/>{round.error && <div className="alert danger">{round.error}</div>}<h4>训练与验证实测</h4><p className="small muted">{round.eligible === undefined ? '尚未完成约束评估' : round.eligible ? '满足固定风险与成交约束' : '未满足固定风险或成交约束'}</p><div className="table-wrap"><table><thead><tr><th>区间</th><th>累计收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>成交笔数</th></tr></thead><tbody>{metricRow('训练集', round.training_metrics)}{metricRow('验证集', round.validation_metrics)}</tbody></table></div>{round.candidate_id && <p className="small muted">候选版本 <code>{round.candidate_id}</code></p>}{round.usage && <details><summary>模型用量</summary><pre>{JSON.stringify(round.usage, null, 2)}</pre></details>}{(round.code || round.params) && <details><summary>完整候选代码与参数（只读）</summary>{round.code && <pre className="code-editor original">{round.code}</pre>}{round.params && <pre>{JSON.stringify(round.params, null, 2)}</pre>}</details>}
      </details>) : <Empty>尚未生成候选轮次</Empty>}</section>
      {result.full_period_note && <div className="alert">{result.full_period_note}</div>}
      {result.candidate_id && <section className="optimization-approval"><div><h3>候选策略 · 人工决策</h3><p className="small muted">版本 <code>{result.candidate_id}</code>。研究结束不会自动批准；批准后仍需手动选择运行版本。</p></div><StrategyApprovalButton strategyId={result.candidate_id} status={strategies.find(row=>row.id===result.candidate_id)?.status} busy={locked || job.status !== 'succeeded'} label="人工批准候选策略" {...approval}/></section>}
      <p className="small muted">成交笔数含买卖各边及再平衡，不是完整往返。最终测试仅在单次任务内隔离；看过同一区间的测试结果后再换策略或目标重做，会失去独立性。目标与历史表现均不是未来收益承诺。</p>
    </>}
  </section>;
}
