import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowRight, RefreshCw, ShieldCheck} from 'lucide-react';
import {api} from './api';
import {Badge, pct, time} from './components';
import {monitorTotals, runState, type MonitorRun, type MonitorStrategy, type StrategyMonitorData} from './strategyMonitor';

const modeLabel = (mode: string | null) => mode === 'demo' ? '演示行情' : mode === 'live' ? '真实行情' : mode || '环境未知';
const orderLabel = (status: string) => ({pending: '待确认', filled: '已成交', cancelled: '已取消', expired: '已过期'}[status] || status);
const attemptLabel = (status: string) => ({queued: '排队中', running: '运行中', finalizing: '处理订单中', succeeded: '已完成', failed: '失败', cancelled: '已取消'}[status] || status);

export function RunRow({run, onOpenJob, onNavigate}: {
  run: MonitorRun;
  onOpenJob: (id: string) => void;
  onNavigate: (page: 'jobs' | 'trading', mode?: string | null) => void;
}) {
  const state = runState(run);
  const pending = run.order_counts.pending || 0;
  const successful = run.job_counts.succeeded || 0;
  const lastAttempt = run.latest_attempt;
  const signal = run.latest_signal;
  const lastOrder = run.latest_order;
  const fill = run.latest_fill;
  return <div className="monitor-run">
    <div className="monitor-run-head">
      <div><strong>{run.symbol || '标的已移除 / 未记录'}</strong><span>{modeLabel(run.mode)} / 虚拟盘{run.currency ? ` / ${run.currency}` : ''}</span></div>
      <span className={'monitor-run-status ' + state.tone}><i aria-hidden="true"/>{state.text}</span>
    </div>
    <div className="monitor-run-grid">
      <div className="monitor-run-cell">
        <span>最近一次信号任务</span>
        <strong>{lastAttempt ? attemptLabel(lastAttempt.status) : '尚未运行'}</strong>
        {lastAttempt && <small>{time(lastAttempt.created_at)} 提交{lastAttempt.status === 'running' && lastAttempt.heartbeat ? ` · 心跳 ${time(lastAttempt.heartbeat)}` : ''}</small>}
        {lastAttempt?.error && <small className="monitor-warning" role="alert">{lastAttempt.error}</small>}
        {lastAttempt && <button className="text-button" onClick={() => onOpenJob(lastAttempt.id)}>查看任务 <ArrowRight size={13}/></button>}
      </div>
      <div className="monitor-run-cell">
        <span>最近一次成功信号</span>
        <strong>{signal ? signal.target_weight === null ? '目标权重未记录' : `目标仓位 ${pct(signal.target_weight)}` : '暂无成功信号'}</strong>
        {signal && <small>完成 {time(signal.finished_at)} · {signal.quote_date ? `行情日期 ${signal.quote_date}` : '行情日期未记录'}{signal.quote_stale ? ' · 行情已过期或日期缺失' : ''}</small>}
        {signal?.source_name && <small>行情来源：{signal.source_name}</small>}
        {signal?.message && <small>{signal.message}</small>}
        {signal && <button className="text-button" onClick={() => onOpenJob(signal.job_id)}>查看信号 <ArrowRight size={13}/></button>}
      </div>
      <div className="monitor-run-cell">
        <span>虚拟盘订单</span>
        <strong>{pending ? `${pending} 笔待确认` : '无待确认订单'}</strong>
        <small>{successful} 次成功信号任务 · {run.order_counts.filled || 0} 笔已成交{run.order_counts.cancelled ? ` · ${run.order_counts.cancelled} 笔已取消` : ''}</small>
        {lastOrder && <small>最近订单：{orderLabel(lastOrder.status)} · {lastOrder.side === 'buy' ? '买入' : lastOrder.side === 'sell' ? '卖出' : lastOrder.side} {lastOrder.quantity} 股 · {time(lastOrder.created_at)}</small>}
        {fill && <small>最近成交：{time(fill.filled_at || fill.created_at)}{fill.fill_price !== null ? ` · 成交价 ${fill.fill_price}` : ''}</small>}
        {(pending > 0 || lastOrder) && <button className="text-button" onClick={() => onNavigate('trading', run.mode)}>查看模拟订单 <ArrowRight size={13}/></button>}
      </div>
    </div>
  </div>;
}

function StrategyRow({strategy, onOpenJob, onNavigate}: {
  strategy: MonitorStrategy;
  onOpenJob: (id: string) => void;
  onNavigate: (page: 'jobs' | 'trading' | 'strategies', mode?: string | null) => void;
}) {
  return <article className="monitor-strategy">
    <div className="monitor-strategy-head">
      <div className="monitor-strategy-name"><div><h4>{strategy.name}</h4><Badge value={strategy.status}/>{strategy.scheduled && <span className="monitor-scheduled">每日信号计划</span>}</div><small>版本 {strategy.id.slice(0, 8)} · {strategy.status === 'approved' ? '已具备人工批准资格，不代表正在运行' : '已撤销批准，仍有待确认订单'}</small></div>
      <button className="text-button" onClick={() => onNavigate('strategies')}>查看策略 <ArrowRight size={14}/></button>
    </div>
    {strategy.blockers.length > 0 && <div className="monitor-blockers" role="status">{strategy.blockers.map(blocker => <span key={blocker}>{blocker}</span>)}</div>}
    {strategy.runs.length ? strategy.runs.map((run, index) => <RunRow key={`${run.mode}-${run.asset_id}-${run.account_id}-${index}`} run={run} onOpenJob={onOpenJob} onNavigate={onNavigate}/>) : <p className="monitor-no-run">尚无模拟信号或订单记录。批准仅开放运行资格；可手动运行信号，或在系统设置配置每日计划。</p>}
  </article>;
}

export function StrategyMonitorPanel({connected, serverTime, onOpenJob, onNavigate}: {
  connected: boolean;
  serverTime: string;
  onOpenJob: (id: string) => void;
  onNavigate: (page: 'jobs' | 'trading' | 'strategies', mode?: string | null) => void;
}) {
  const [data, setData] = useState<StrategyMonitorData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [heartbeat, setHeartbeat] = useState(() => Date.now());
  const request = useRef<Promise<void> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const lastRequested = useRef(0);
  const active = useRef(true);
  const load = useCallback((force = false) => {
    if (!force && (document.hidden || Date.now() - lastRequested.current < 10000)) return request.current || Promise.resolve();
    if (request.current) return request.current;
    lastRequested.current = Date.now();
    if (active.current) setLoading(true);
    const abort = new AbortController();
    controller.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 12000);
    const pending = api<StrategyMonitorData>('/strategy-monitor', undefined, undefined, abort.signal).then(result => {
      if (!active.current || controller.current !== abort) return;
      setData(result);
      setError('');
      setUpdatedAt(Date.now());
      setHeartbeat(Date.now());
    }).catch(failure => {
      if (active.current && controller.current === abort) setError(abort.signal.aborted ? '请求超时，请重试' : (failure as Error).message);
    }).finally(() => {
      window.clearTimeout(timeout);
      if (controller.current === abort) {
        controller.current = null;
        request.current = null;
        if (active.current) setLoading(false);
      }
    });
    request.current = pending;
    return pending;
  }, []);
  useEffect(() => {
    active.current = true;
    void load(true);
    const timer = window.setInterval(() => {setHeartbeat(Date.now());void load();}, 15000);
    const visible = () => {if (!document.hidden) void load(true);};
    document.addEventListener('visibilitychange', visible);
    return () => {
      active.current = false;
      controller.current?.abort();
      controller.current = null;
      request.current = null;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [load]);
  useEffect(() => {if (serverTime) void load();}, [serverTime, load]);
  const totals = data ? monitorTotals(data) : null;
  const stale = updatedAt !== null && heartbeat - updatedAt > 30000;
  return <section className="panel strategy-monitor" aria-labelledby="strategy-monitor-title" aria-busy={loading && !data}>
    <div className="monitor-heading">
      <div><h2 id="strategy-monitor-title">策略运行监控 <span className="count">{totals?.approved ?? '—'}</span></h2><p>逐版本查看虚拟盘信号与订单；覆盖全部已批准策略，不受上方行情环境与币种筛选影响。</p></div>
      <div className="monitor-controls"><span className="monitor-updated" role="status">{error ? `${data ? '更新失败 · 旧数据' : '更新失败'}${updatedAt ? ` · 上次更新 ${time(new Date(updatedAt).toISOString())}` : ''}` : updatedAt ? `${stale ? '数据已过期' : connected ? '推送与轮询' : '轮询'} · 更新于 ${time(new Date(updatedAt).toISOString())}` : '正在读取运行记录'}</span><button type="button" aria-label="刷新策略运行监控" title="刷新策略运行监控" disabled={loading} onClick={() => {void load(true);}}><RefreshCw size={15} className={loading ? 'is-spinning' : ''}/>刷新</button></div>
    </div>
    {error && <div className="monitor-fetch-error" role="alert">监控数据更新失败：{error}。{data ? '下方为上次成功读取的数据，请刷新重试。' : '请检查连接后重试。'}</div>}
    {!data ? <div className="monitor-loading" role="status">{loading ? '正在读取所有已批准策略的虚拟盘记录…' : '暂时无法读取运行监控。'}</div> : <>
      <div className="monitor-overview" aria-label="运行汇总"><span><b>{totals?.approved}</b> 个已批准版本</span><span><b>{totals?.inProgress}</b> 个排队或处理中信号任务</span><span><b>{totals?.pending}</b> 笔待确认虚拟订单</span><span className="monitor-no-live"><ShieldCheck size={15}/>{data.real_trading ? '实盘能力已启用；此处仅显示虚拟盘' : '券商实盘未接入'}</span></div>
      {data.strategies.length ? <div className="monitor-list">{data.strategies.map(strategy => <StrategyRow key={strategy.id} strategy={strategy} onOpenJob={onOpenJob} onNavigate={onNavigate}/>)}</div> : <div className="monitor-loading">暂无已批准策略。请先回测或完成候选评估，再到策略工作台人工批准。<button className="text-button" onClick={() => onNavigate('strategies')}>前往策略工作台 <ArrowRight size={13}/></button></div>}
      <div className="monitor-footnote">{data.note} 已撤销批准但仍有待确认订单的版本继续显示，直到待办状态解除。页面可见时约每15秒轮询，推送刷新至多每10秒一次；刷新时间不代表行情实时更新。</div>
    </>}
  </section>;
}
