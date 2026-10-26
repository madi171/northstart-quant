import {ArrowRight, Check, Circle, Clock3, FlaskConical, Plus, ShieldCheck, Sparkles} from 'lucide-react';
import type {ReactNode} from 'react';
import {Badge, Chart, Empty, money, pct} from './components';
import {StrategyMonitorPanel} from './StrategyMonitorPanel';
import type {Account, Market, State} from './types';

interface DashboardProps {
  state: State;
  account?: Account;
  mode: string;
  currency: string;
  markets: Market[];
  marketLoading: boolean;
  marketError: string;
  recentJobs: ReactNode;
  connected: boolean;
  onOpenJob: (id: string) => void;
  onCurrency: (currency: string) => void;
  onNavigate: (page: 'assets' | 'strategies' | 'backtests' | 'trading' | 'models' | 'jobs', mode?: string | null) => void;
  onAddAsset: () => void;
}

function MarketTrace({prices, name}: {prices: number[]; name: string}) {
  if (prices.length < 2) return null;
  const low = Math.min(...prices);
  const range = Math.max(...prices) - low || 1;
  const points = prices.map((price, index) => `${index * 96 / (prices.length - 1)},${30 - (price - low) * 26 / range}`);
  return (
    <svg className="market-trace" viewBox="0 0 96 34" role="img" aria-label={`${name}近期价格走势`}>
      <polyline points={points.join(' ')} fill="none" stroke="currentColor" strokeWidth="1.7"/>
    </svg>
  );
}

export function Dashboard({
  state, account, mode, currency, markets, marketLoading, marketError, recentJobs, connected, onOpenJob, onCurrency, onNavigate, onAddAsset,
}: DashboardProps) {
  const holdings = account ? Object.values(account.positions).filter(position => position.quantity > 0) : [];
  const equity = account ? account.cash + holdings.reduce((sum, position) => sum + position.price * position.quantity, 0) : undefined;
  const returns = account && equity !== undefined && account.initial > 0 ? equity / account.initial - 1 : undefined;
  const enabledAssets = state.assets.filter(asset => asset.enabled).length;
  const completed = state.jobs.filter(job => job.kind === 'backtest' && job.status === 'succeeded' && job.summary.data_mode === mode);
  const running = state.jobs.filter(job => job.status === 'running').length;
  const queued = state.jobs.filter(job => job.status === 'queued').length;
  const pending = state.orders.filter(order => order.data_mode === mode && order.status === 'pending').length;
  const approved = state.strategies.filter(strategy => strategy.status === 'approved').length;
  const steps = [
    {title: '选择标的', detail: `${enabledAssets} 个启用标的`, page: 'assets' as const, ready: enabledAssets > 0},
    {title: '研究策略', detail: `${state.strategies.length} 个策略版本`, page: 'strategies' as const, ready: state.strategies.length > 0},
    {title: '回测验证', detail: `${completed.length} 次完成回测`, page: 'backtests' as const, ready: completed.length > 0},
    {title: '人工确认', detail: `${pending} 笔订单待确认`, page: 'trading' as const, ready: false},
  ];
  const next = !enabledAssets
    ? {title: '从一个投资标的开始', text: '维护你的研究范围，再为它选择策略。系统不会自动添加标的。', action: '添加投资标的', run: onAddAsset}
    : !state.strategies.length
      ? {title: '为标的选择研究策略', text: '从策略模板开始，或编写自己的 Python 策略。每个版本独立保存。', action: '前往策略工作台', run: () => onNavigate('strategies')}
      : {title: '用回测检验你的想法', text: '在相同条件下比较策略，保留数据快照与评估证据，再决定是否批准。', action: '查看回测实验室', run: () => onNavigate('backtests')};

  return (
    <div className="dashboard">
      <StrategyMonitorPanel connected={connected} serverTime={state.server_time} onOpenJob={onOpenJob} onNavigate={onNavigate}/>
      <div className="dashboard-primary">
        <section className="panel account-panel" aria-labelledby="account-title">
          <div className="panel-heading">
            <div className="panel-title"><span className="panel-marker"/><h2 id="account-title">模拟账户</h2><Badge value={mode}/></div>
            <div className="currency-switch" role="group" aria-label="账户币种">
              {['CNY', 'HKD', 'USD'].map(value => (
                <button key={value} type="button" aria-pressed={currency === value} onClick={() => onCurrency(value)}>{value}</button>
              ))}
            </div>
          </div>
          <div className="account-summary">
            <div className="account-balance"><span>总资产估值 <small>{currency}</small></span><strong>{money(equity)}</strong></div>
            <dl className="account-facts">
              <div><dt>累计收益率</dt><dd className={returns === undefined ? '' : returns >= 0 ? 'positive' : 'negative'}>{pct(returns)}</dd></div>
              <div><dt>可用资金</dt><dd>{money(account?.cash)}</dd></div>
              <div><dt>持仓标的</dt><dd>{account ? holdings.length : '—'}<small> 个</small></dd></div>
            </dl>
          </div>
          <div className="equity-heading"><span><i/>账户净值</span><small>成交或信号估值时记录，非实时行情</small></div>
          {mode === 'historical' ? <Empty>历史回放不关联模拟账户。前往回测实验室查看实验净值。</Empty> : <Chart points={account?.curve || []} benchmark={false}/>}
          <div className="account-disclaimer"><ShieldCheck size={14}/><span>按币种独立核算，不合并不同币种资产；模拟收益不代表真实投资表现。</span></div>
        </section>
        <aside className="research-rail" aria-label="研究与待办">
          <section className="next-research">
            <div className="research-emblem"><FlaskConical size={22}/><span>下一步研究</span></div>
            <h2>{next.title}</h2>
            <p>{next.text}</p>
            <button className="primary" onClick={next.run}><Plus size={16}/>{next.action}</button>
            <div className="research-safety"><ShieldCheck size={14}/>研究不自动批准，不自动下单</div>
          </section>
          <section className="panel attention-panel">
            <div className="panel-heading"><h3>需要关注</h3><Clock3 size={16}/></div>
            <button className="attention-row" onClick={() => onNavigate('jobs')}>
              <span><i className={running ? 'activity-dot is-running' : 'activity-dot'}/>任务运行中<small>{queued} 个排队等待</small></span><strong>{running}</strong><ArrowRight size={15}/>
            </button>
            <button className="attention-row" onClick={() => onNavigate('trading')}>
              <span>订单待确认<small>仅人工确认后模拟成交</small></span><strong className={pending ? 'pending-number' : ''}>{pending}</strong><ArrowRight size={15}/>
            </button>
          </section>
        </aside>
      </div>
      <section className="panel research-path" aria-labelledby="path-title">
        <div className="path-heading"><h3 id="path-title">研究路径</h3><span>从想法到证据，由你确认</span></div>
        <div className="path-steps">
          {steps.map((step, index) => (
            <button key={step.page} onClick={() => onNavigate(step.page)}>
              <span className={'path-node ' + (step.ready ? 'is-ready' : '')}>{step.ready ? <Check size={15}/> : <Circle size={14}/>}</span>
              <span className="path-copy"><strong>{step.title}</strong><small>{step.detail}</small></span>
              <ArrowRight className="path-arrow" size={15}/><span className="sr-only">第 {index + 1} 步</span>
            </button>
          ))}
        </div>
      </section>
      <div className="dashboard-secondary">
        <section className="panel recent-jobs"><div className="panel-heading"><div className="panel-title"><h3>最近任务</h3><span className="count">{state.jobs.length}</span></div><button className="text-button" onClick={() => onNavigate('jobs')}>全部记录 <ArrowRight size={14}/></button></div>{recentJobs}</section>
        <section className="panel market-panel">
          <div className="panel-heading"><h3>市场观察</h3>{mode==='demo'?<Badge value={mode}/>:<span className="tag">延迟指数报价</span>}</div>
          {marketLoading&&<p role="status" className="small muted">正在更新指数报价…</p>}
          {marketError&&<p role="alert">指数更新失败：{marketError}</p>}
          {markets.length ? <div className="market-list">{markets.map(market => (
            <div className="market-row" key={market.symbol}>
              <div className="market-identity"><strong>{market.name}{market.stale?' · 旧报价':''}</strong><small>{market.quoted_at?.replace('T',' ') || market.date || '—'} · {market.synthetic ? '合成行情' : market.source || '来源未知'}</small>{market.fetched_at&&<small title={market.fetched_at}>获取时间 {new Date(market.fetched_at).toLocaleTimeString('zh-CN',{hour12:false})}（本机时间）</small>}</div>
              {market.prices && <div className={(market.change || 0) >= 0 ? 'positive' : 'negative'}><MarketTrace prices={market.prices} name={market.name}/></div>}
              <div className="market-price"><strong>{money(market.price)}</strong><small className={market.change === undefined ? '' : market.change >= 0 ? 'positive' : 'negative'}>{pct(market.change)}</small></div>
              {market.error && <p>{market.error}</p>}{market.warning&&<p role="status">{market.warning}</p>}{market.trend && <p>{market.trend}</p>}
            </div>
          ))}</div> : <Empty>{marketLoading?'正在获取指数行情…':'暂无可用指数报价，请稍后刷新。'}</Empty>}
          <div className="panel-note">首页可见时每分钟刷新，也可使用顶部刷新按钮。{mode==='demo'?'当前为合成演示行情。':'公开行情可能延迟；休市时保留最近报价，涨跌幅相对昨收。独立于历史回放和交易数据源。'}仅供观察，不用于回测或成交。</div>
        </section>
      </div>
      <section className="model-invitation">
        <div className="model-invitation-icon"><Sparkles size={21}/></div><div><h3>模型负责探索，你保留判断。</h3><p>{approved} 个已批准策略版本。模型候选须经过独立评估与人工审批。</p></div>
        <button onClick={() => onNavigate('models')}>配置模型协作 <ArrowRight size={15}/></button>
      </section>
    </div>
  );
}
