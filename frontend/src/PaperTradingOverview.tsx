import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowRight, RefreshCw, ShieldCheck} from 'lucide-react';
import {api} from './api';
import {Badge, money, pct, time} from './components';
import {runState, type MonitorRun, type MonitorStrategy, type StrategyMonitorData} from './strategyMonitor';

export interface PaperPortfolio {
  account_id: string;
  mode: 'demo' | 'live';
  currency: string;
  cash: number | null;
  initial: number | null;
  equity: number | null;
  return_rate: number | null;
  complete: boolean;
  valuation_at: string;
  note: string;
  real_trading: false;
  positions: {asset_id: string; symbol: string; quantity: number | null; price: number | null;
    quote_date: string | null; source: string | null; fresh: boolean; market_value: number | null}[];
}

export function paperReturn(portfolio: PaperPortfolio | null, mode: string, currency: string, stale: boolean, error: string) {
  if (!portfolio || portfolio.mode !== mode || portfolio.currency !== currency || !portfolio.complete || stale || error) return null;
  return portfolio.return_rate;
}

export function paperDecisionRows(monitor: StrategyMonitorData | null, mode: string, currency: string) {
  return monitor?.strategies.flatMap<{strategy: MonitorStrategy; run: MonitorRun | null}>(strategy => {
    const matching = strategy.runs.filter(run => run.mode === mode && run.currency === currency);
    return matching.length ? matching.map(run => ({strategy, run})) : strategy.status === 'approved'
      ? [{strategy, run: null}] : [];
  }) || [];
}

export function PaperTradingOverview({mode, currency, serverTime, onCurrency, onOpenJob, onRunSignal, canRun}: {
  mode: string;
  currency: string;
  serverTime: string;
  onCurrency: (currency: string) => void;
  onOpenJob: (id: string) => void;
  onRunSignal: () => void;
  canRun: boolean;
}) {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [monitor, setMonitor] = useState<StrategyMonitorData | null>(null);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState('');
  const [clock, setClock] = useState(() => Date.now());
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);
  const active = useRef(false);
  const sequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const lastRequested = useRef(0);
  const load = useCallback(async (force = false) => {
    if (inFlight.current || !active.current || document.hidden || mode === 'historical') return;
    if (!force && Date.now() - lastRequested.current < 14000) return;
    inFlight.current = true;
    lastRequested.current = Date.now();
    const generation = sequence.current;
    setLoading(true);
    const abort = new AbortController();
    requestController.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 12000);
    try {
      const [accountResult, monitorResult] = await Promise.allSettled([
        api<PaperPortfolio>(`/paper-portfolio?mode=${encodeURIComponent(mode)}&currency=${encodeURIComponent(currency)}`, undefined, undefined, abort.signal),
        api<StrategyMonitorData>('/strategy-monitor', undefined, undefined, abort.signal),
      ]);
      if (!active.current || generation !== sequence.current || requestController.current !== abort) return;
      if (accountResult.status === 'fulfilled' && monitorResult.status === 'fulfilled') {
        setPortfolio(accountResult.value);
        setMonitor(monitorResult.value);
        setError('');
        setUpdatedAt(new Date().toISOString());
        setClock(Date.now());
      } else {
        const failures = [accountResult, monitorResult].filter(result => result.status === 'rejected');
        setError(abort.signal.aborted ? '请求超时，请重试' : failures.map(result => result.reason instanceof Error ? result.reason.message : '请求失败').join('；'));
      }
    } finally {
      window.clearTimeout(timeout);
      if (generation === sequence.current && requestController.current === abort) {
        requestController.current = null;
        inFlight.current = false;
        setLoading(false);
      }
    }
  }, [mode, currency]);
  useEffect(() => {
    active.current = true;
    sequence.current++;
    requestController.current?.abort();
    requestController.current = null;
    inFlight.current = false;
    setPortfolio(null);
    setMonitor(null);
    setError('');
    setUpdatedAt('');
    lastRequested.current = 0;
    if (mode !== 'historical') void load(true);
    const timer = window.setInterval(() => {setClock(Date.now());void load();}, 15000);
    const visible = () => {if (!document.hidden) void load(true);};
    document.addEventListener('visibilitychange', visible);
    return () => {
      active.current = false;
      sequence.current++;
      requestController.current?.abort();
      requestController.current = null;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [load, mode]);
  useEffect(() => {if (serverTime) void load();}, [serverTime, load]);
  const stale = Boolean(updatedAt && clock - new Date(updatedAt).getTime() > 30000);
  const unavailable = Boolean(error || stale);
  const visiblePortfolio = portfolio?.mode === mode && portfolio.currency === currency ? portfolio : null;
  const displayedReturn = paperReturn(visiblePortfolio, mode, currency, stale, error);
  const rows = paperDecisionRows(monitor, mode, currency);
  return <div className="paper-overview">
    {mode === 'historical' ? <div className="panel paper-intro"><h2>历史回放没有模拟账户</h2><p>请选择演示或真实行情环境。历史回测的收益和成交记录在回测实验室，不并入虚拟盘。</p></div> : <>
      <section className="panel paper-summary" aria-labelledby="paper-summary-title">
        <div className="paper-summary-head"><div><h2 id="paper-summary-title">共享模拟账户</h2><p>{mode === 'demo' ? '合成演示日线' : '真实行情环境 · 本地未复权已完成日线'} / {currency}</p></div><div className="paper-tools"><label>账户币种 <select aria-label="模拟账户币种" value={currency} onChange={event => onCurrency(event.target.value)}>{['CNY','HKD','USD'].map(value => <option key={value}>{value}</option>)}</select></label><span role="status">{error ? '更新失败 · 请核对报价' : stale ? '数据已过期 · 请刷新' : updatedAt ? `读取于 ${time(updatedAt)}` : '正在读取账户'}</span><button onClick={() => {void load(true);}} disabled={loading} aria-label="刷新模拟盘"><RefreshCw size={15} className={loading ? 'is-spinning' : ''}/>刷新</button></div></div>
        {error && <div className="paper-error" role="alert">部分数据读取失败：{error}。下方保留上次记录，账户收益已暂停显示。</div>}
        {!visiblePortfolio ? <p className="paper-placeholder">{loading ? '正在读取账户和报价…' : '账户估值暂不可用；请检查连接后重试。'}</p> : <>
          <div className="paper-balance"><div className="paper-main-figure"><span>账户权益估值</span><strong>{money(unavailable ? undefined : visiblePortfolio.equity ?? undefined)}</strong><small>{unavailable ? '监控未同步，暂停显示账户权益与收益；请刷新' : visiblePortfolio.equity === null ? '存在缺失或过期报价，暂不计算总权益' : `${currency} · 已完成日线估值，非实时行情`}</small></div><dl><div><dt>累计收益率</dt><dd className={displayedReturn == null ? '' : displayedReturn >= 0 ? 'positive' : 'negative'}>{pct(displayedReturn ?? undefined)}</dd></div><div><dt>账户现金</dt><dd>{money(visiblePortfolio.cash ?? undefined)}</dd></div><div><dt>初始资金</dt><dd>{money(visiblePortfolio.initial ?? undefined)}</dd></div></dl></div>
          <div className="paper-summary-note"><ShieldCheck size={15}/><span>{visiblePortfolio.note} 行情只读刷新不会更新模拟账户或自动成交。</span></div>
        </>}
      </section>
      <section className="panel paper-decisions" aria-labelledby="paper-decisions-title"><div className="paper-section-heading"><div><h2 id="paper-decisions-title">策略决策与执行</h2><p>每个策略版本的最近信号、目标仓位和模拟订单；不把共享账户收益归给单个策略。</p></div><button onClick={onRunSignal} disabled={!canRun} className="primary">运行已批准策略信号</button></div>
        {monitor ? rows.length ? <div className="paper-decision-list">{rows.map(({strategy,run},index) => {
          const status = run ? runState(run) : {text: '尚无信号记录', tone: 'quiet'};
          const signal = run?.latest_signal;
          const attempt = run?.latest_attempt;
          return <article className="paper-decision" key={`${strategy.id}-${run?.asset_id ?? 'unstarted'}-${run?.account_id ?? currency}-${index}`}><div className="paper-decision-title"><div><strong>{strategy.name}</strong><small>{run?.symbol || '尚未选择运行标的'} · {strategy.id.slice(0,8)} · {strategy.scheduled ? '已配置每日计划' : '无每日计划'}</small></div><Badge value={strategy.status}/></div><div className="paper-decision-fields"><div><span>最近任务</span><strong className={status.tone === 'warning' ? 'negative' : ''}>{status.text}</strong><small>{time(attempt?.created_at)}</small>{attempt?.error && <small className="negative" role="alert">{attempt.error}</small>}</div><div><span>最近一次成功决策</span><strong>{signal ? signal.target_weight === null ? '权重未记录' : `目标仓位 ${pct(signal.target_weight)}` : '尚无信号'}</strong><small>{signal?.quote_date ? `行情 ${signal.quote_date}${signal.quote_stale ? '（过期）' : ''}` : '行情日期未记录'}{signal?.price != null ? ` · 收盘 ${money(signal.price)}` : ''}</small><small>{signal?.source_name ? `数据源 ${signal.source_name} · ` : ''}完成于 {time(signal?.finished_at)}</small><small>{signal?.message || '信号不等于成交'}</small></div><div><span>模拟执行</span><strong>{run?.order_counts.pending ? `${run.order_counts.pending} 笔待确认` : run?.latest_order ? `最近订单 ${run.latest_order.status === 'filled' ? '已成交' : run.latest_order.status === 'cancelled' ? '已取消' : run.latest_order.status}` : '尚无订单'}</strong><small>累计已成交 {run?.order_counts.filled || 0} 笔</small></div></div>{attempt && <button className="text-button" onClick={() => onOpenJob(attempt.id)}>查看本次任务 <ArrowRight size={14}/></button>}{strategy.blockers.length > 0 && <p className="paper-decision-warning">{strategy.blockers.join('；')}</p>}</article>;
        })}</div> : <p className="paper-placeholder">该环境与币种暂无已批准策略的信号或订单。人工批准只开放模拟信号资格；不代表策略已在运行。</p> : <p className="paper-placeholder">{loading ? '正在读取策略决策…' : '决策记录暂不可用，请刷新重试。'}</p>}
      </section>
      <section className="panel paper-positions" aria-labelledby="paper-positions-title"><div className="paper-section-heading"><div><h2 id="paper-positions-title">模拟持仓估值</h2><p>按账户持有数量估值；报价日期逐项列示，缺价或过期时不推断收益。</p></div></div>{visiblePortfolio?.positions.length ? <div className="table-wrap"><table><thead><tr><th>标的</th><th>数量</th><th>估值价 / 来源</th><th>市值 · {currency}</th><th>报价日期</th></tr></thead><tbody>{visiblePortfolio.positions.map(position => <tr key={position.asset_id}><td><strong>{position.symbol}</strong></td><td>{position.quantity ?? '—'}</td><td>{money(position.price ?? undefined)}<small>{position.source || '报价缺失'}</small></td><td>{money(position.market_value ?? undefined)}</td><td>{position.quote_date || '—'}<small className={position.fresh ? '' : 'negative'}>{position.fresh ? '有效的已完成日线' : '无有效报价'}</small></td></tr>)}</tbody></table></div> : <p className="paper-placeholder">{loading ? '正在读取当前模拟账户持仓…' : '暂无模拟持仓。生成策略信号并人工确认模拟订单后，此处才会出现持仓。'}</p>}</section>
    </>}
  </div>;
}
