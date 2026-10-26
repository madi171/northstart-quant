import {useEffect, useMemo, useRef, useState, type ReactNode} from 'react';
import {createStateRefresher} from './stateRefresh';
import {StrategyApprovalButton, type ApprovalPending} from './StrategyApprovalButton';
import {Activity, ArrowDownToLine, ArrowRight, Bell, BookOpen, Check, ChevronRight, CircleDollarSign, Clock3, Code2, Compass, Database, FlaskConical, LayoutDashboard, LogOut, Plus, RefreshCw, Settings2, ShieldCheck, Sparkles, Square, Star, X} from 'lucide-react';
import {Dashboard} from './Dashboard';
import {PaperTradingOverview} from './PaperTradingOverview';
import {BacktestProgressPanel} from './BacktestProgressPanel';
import {SignalLibrary} from './SignalLibrary';
import {api, clearToken, download, saveToken, token} from './api';
import {createClientKey} from './clientKey';
import {readDataMode, saveDataMode} from './dataMode';
import {holdingValue, sortAssetsByValue, type AssetValuation} from './assetValuation';
import type {State, Job, Strategy, Provider, Asset, Market, News, Risk, Schedule, Metrics} from './types';
import {ThemeToggle} from './theme';
import {ReportChart} from './ReportChart';
import {ExperimentRevision} from './ExperimentRevision';
import {StrategyLibrary} from './StrategyLibrary';
import {SignalDiagnosis} from './SignalDiagnosis';
import {OptimizationWorkbench} from './OptimizationWorkbench';
import {OptimizationReport} from './OptimizationReport';
import {NamingSource, StrategyDiff, StrategyExplanation} from './ResearchEvidence';
import {ResearchTaskContext} from './ResearchTaskContext';
import {researchManifest} from './researchManifest';
import {StrategyStudyReport} from './StrategyStudyReport';
import {HelpWiki} from './HelpWiki';
import './optimization.css';
import {Badge, Empty, MetricsGrid, Modal, money, pct, time} from './components';

type Page='overview'|'autoresearch'|'assets'|'signals'|'strategies'|'backtests'|'trading'|'sources'|'models'|'jobs'|'settings'|'help';
const pages:{id:Page;name:string;icon:typeof Activity;en:string}[]=[
  {id:'overview',name:'工作台总览',icon:LayoutDashboard,en:'Workspace overview'},
  {id:'autoresearch',name:'Auto Research',icon:Sparkles,en:'策略自主研究'},
  {id:'assets',name:'投资标的',icon:Star,en:'Your investment universe'},
  {id:'signals',name:'信号与特征',icon:Activity,en:'News signals & research features'},
  {id:'strategies',name:'策略工作台',icon:Code2,en:'Strategy research & versions'},
  {id:'backtests',name:'回测实验室',icon:FlaskConical,en:'Backtesting laboratory'},
  {id:'trading',name:'交易与持仓',icon:CircleDollarSign,en:'Paper execution & positions'},
  {id:'sources',name:'行情与消息源',icon:Database,en:'Market data & information'},
  {id:'models',name:'模型协作',icon:Sparkles,en:'Model orchestration'},
  {id:'jobs',name:'任务运行记录',icon:Clock3,en:'Task history & observability'},
  {id:'settings',name:'系统设置',icon:Settings2,en:'Risk controls & schedules'},
  {id:'help',name:'帮助 Wiki',icon:BookOpen,en:'操作手册与示例'},
];
const today=()=>new Date().toISOString().slice(0,10);
const yearAgo=()=>new Date(Date.now()-365*86400000).toISOString().slice(0,10);

export default function App(){
  const [logged,setLogged]=useState(!!token()),[password,setPassword]=useState('');
  const [state,setState]=useState<State|null>(null),[page,setCurrentPage]=useState<Page>(()=>location.hash.startsWith('#help/')?'help':'overview');
  useEffect(()=>{const change=()=>setCurrentPage(previous=>location.hash.startsWith('#help/')?'help':previous==='help'?'overview':previous);window.addEventListener('hashchange',change);return()=>window.removeEventListener('hashchange',change)},[]);
  const [mode,setMode]=useState<string>(readDataMode),[currency,setCurrency]=useState('CNY'),[connected,setConnected]=useState(false);
  function setPage(next:Page){
    if(next==='autoresearch'&&page!=='autoresearch')setMode('historical');
    if(next==='help'&&!location.hash.startsWith('#help/'))location.hash='help/quickstart';
    if(next!=='help'&&location.hash.startsWith('#help/'))history.replaceState(null,'',location.pathname+location.search);
    setCurrentPage(next);
  }
  useEffect(()=>saveDataMode(mode),[mode]);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const [markets,setMarkets]=useState<Market[]>([]),[news,setNews]=useState<News[]>([]),[sourceFailures,setSourceFailures]=useState('');
  const [marketRefresh,setMarketRefresh]=useState(0),[marketLoading,setMarketLoading]=useState(false),[marketError,setMarketError]=useState('');
  const [valuations,setValuations]=useState<AssetValuation[]>([]);
  const [valuationError,setValuationError]=useState('');
  useEffect(()=>{
    if(!logged||page!=='assets')return;
    let active=true;
    const load=()=>api<AssetValuation[]>('/assets/valuations').then(rows=>{if(active){setValuations(rows);setValuationError('')}}).catch(e=>{if(active){setValuations([]);setValuationError(e.message)}});
    load();const timer=setInterval(load,60000);
    return()=>{active=false;clearInterval(timer)};
  },[logged,page,JSON.stringify(state?.assets),JSON.stringify(state?.providers)]);
  const [modal,setModal]=useState<'asset'|'strategy'|'run'|'provider'|null>(null);
  const [selectedAsset,setSelectedAsset]=useState<Asset|null>(null);
  const [selectedStrategy,setSelectedStrategy]=useState<Strategy|null>(null),[selectedProvider,setSelectedProvider]=useState<Provider|null>(null);
  const [runKind,setRunKind]=useState('backtest'),[reportId,setReportId]=useState(''),[report,setReport]=useState<Job|null>(null);
  const [runStrategyId,setRunStrategyId]=useState('');
  const refresh=useMemo(()=>createStateRefresher(()=>api<State>('/state'),setState),[]);
  const [approvalPending,setApprovalPending]=useState<ApprovalPending>(null);
  const approvalLock=useRef(false);
  const act=async(fn:()=>Promise<unknown>,success='操作已保存')=>{setBusy(true);setError('');try{await fn();await refresh(true);setNotice(success);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  async function changeApproval(id:string,revoke:boolean){
    if(approvalLock.current||busy)return;
    approvalLock.current=true;setApprovalPending({id,action:revoke?'revoke':'approve'});setBusy(true);setError('');
    let updated:Strategy|undefined;
    try{
      updated=await api<Strategy>('/strategies/'+id+(revoke?'/revoke':'/approve'),{});
      await refresh(true);
      setNotice(revoke?'已撤销批准；不自动平仓，历史记录保留':'策略已人工批准；不会自动运行或下单');
    }catch(e){
      if(updated){const saved=updated;setState(previous=>previous?{...previous,strategies:previous.strategies.map(row=>row.id===id?saved:row)}:previous)}
      setError((updated?'操作已完成，但列表同步失败，请刷新核对：':'')+(e as Error).message);
    }finally{approvalLock.current=false;setApprovalPending(null);setBusy(false)}
  }
  const approval={pending:approvalPending,onChange:changeApproval};
  useEffect(()=>{const fn=()=>{setLogged(false);setState(null)};window.addEventListener('quant-logout',fn);return()=>window.removeEventListener('quant-logout',fn)},[]);
  useEffect(()=>{
    const sync=(event:StorageEvent)=>{
      if(event.key!=='quant-token')return;
      if(event.newValue){setLogged(true);setError('');}
      else {setLogged(false);setState(null);}
    };
    window.addEventListener('storage',sync);
    return()=>window.removeEventListener('storage',sync);
  },[]);
  useEffect(()=>{if(notice){const id=setTimeout(()=>setNotice(''),5000);return()=>clearTimeout(id)}},[notice]);
  useEffect(()=>{
    if(!logged)return;
    const control=new AbortController();
    refresh().catch(e=>setError(e.message));
    const listen=async()=>{
      while(!control.signal.aborted){
        try{
          const response=await fetch('/api/events',{headers:{Authorization:'Bearer '+token()},signal:control.signal});
          if(response.status===401){window.dispatchEvent(new Event('quant-logout'));return;}
          if(!response.ok||!response.body)throw new Error('推送连接失败');
          setConnected(true);const reader=response.body.getReader();
          while(!control.signal.aborted){const {done}=await reader.read();if(done)break;await refresh();}
        }catch{if(control.signal.aborted)return;}finally{setConnected(false)}
        await new Promise(resolve=>setTimeout(resolve,3000));
      }
    };listen();return()=>control.abort();
  },[logged,refresh]);
  useEffect(()=>{
    if(!logged||(page!=='overview'&&(page!=='trading'||mode==='historical')))return;
    let active=true,pending=false;
    setMarkets([]);setMarketError('');
    const load=async()=>{
      if(pending||document.hidden)return;
      pending=true;setMarketLoading(true);
      try{const rows=await api<Market[]>('/market?mode='+mode);if(active){setMarkets(rows);setMarketError('')}}
      catch(e){if(active){setMarketError((e as Error).message);setMarkets(rows=>rows.map(row=>({...row,stale:true,warning:'刷新失败，显示旧报价；请稍后重试。'})))}}
      finally{pending=false;if(active)setMarketLoading(false)}
    };
    void load();const timer=setInterval(load,60000);
    document.addEventListener('visibilitychange',load);
    return()=>{active=false;clearInterval(timer);document.removeEventListener('visibilitychange',load)};
  },[logged,page,mode,marketRefresh,JSON.stringify(state?.providers)]);
  useEffect(()=>{if(logged&&(page==='sources'||page==='signals'))api<{items:News[];failures:unknown[]}>('/news').then(x=>{setNews(x.items);setSourceFailures(x.failures.length?JSON.stringify(x.failures):'')}).catch(e=>setError(e.message))},[logged,page,JSON.stringify(state?.providers)]);
  useEffect(()=>{if(!reportId)return;let active=true;api<Job>('/jobs/'+reportId).then(x=>{if(active)setReport(x)}).catch(e=>setError(e.message));return()=>{active=false}},[reportId,state?.server_time]);
  useEffect(()=>{
    if(!reportId||report?.id!==reportId||report.kind!=='backtest'||!['queued','running','finalizing'].includes(report.status))return;
    let active=true,pending=false;
    const timer=window.setInterval(async()=>{
      if(pending||document.hidden)return;
      pending=true;
      try{const current=await api<Job>('/jobs/'+reportId);if(active)setReport(current)}
      catch(e){if(active)setError((e as Error).message)}
      finally{pending=false}
    },2000);
    return()=>{active=false;window.clearInterval(timer)};
  },[reportId,report?.id,report?.kind,report?.status]);
  function openAsset(asset:Asset|null){setSelectedAsset(asset);setModal('asset')}
  function openRun(kind:string){setRunStrategyId('');setRunKind(kind);setModal('run')}
  function templateAdded(strategy:Strategy){setState(previous=>previous?{...previous,strategies:[strategy,...previous.strategies.filter(item=>item.id!==strategy.id)]}:previous);setNotice('研究策略已添加；请在回测实验室或 Auto Research 中选择使用');refresh().catch(e=>setError(e.message))}
  function openStrategy(strategy:Strategy|null){setSelectedStrategy(strategy);setModal('strategy')}
  function openProvider(provider:Provider|null){setSelectedProvider(provider);setModal('provider')}
  function openReport(id:string){setReport(null);setReportId(id)}
  function navigateFromMonitor(next:Page,monitorMode?:string|null){
    if(next==='trading'&&(monitorMode==='demo'||monitorMode==='live'))setMode(monitorMode);
    setPage(next);
  }
  function logout(){clearToken();setLogged(false);setState(null);setPassword('')}
  const formAction=(fn:()=>Promise<unknown>,success?:string)=>act(fn,success);

  if(!logged)return <div className="login-shell"><div className="login-theme"><ThemeToggle/></div><div className="login-brand"><div className="login-wordmark"><div className="brand-symbol"><Compass size={25}/></div><span>Northstar</span></div><div className="login-story"><span className="login-caption">个人量化研究工作台</span><h1>先验证想法，<br/>再作出判断。</h1><p>把标的、策略和实验放在同一个研究空间。<br/>每一个决定，都能回到它的证据。</p><div className="login-grid"><span><Star size={17}/>维护研究标的</span><span><Code2 size={17}/>编写策略版本</span><span><FlaskConical size={17}/>复现回测实验</span><span><ShieldCheck size={17}/>人工审核确认</span></div></div><small><ShieldCheck size={14}/>仅研究与模拟交易，不接入实盘</small></div><form className="login-card" onSubmit={e=>{e.preventDefault();act(async()=>{const r=await api<{token:string}>('/login',{password});saveToken(r.token);setLogged(true)},'登录成功')}}><div className="login-form-icon"><Compass size={25}/></div><h2>回到你的研究空间</h2><p className="muted">使用部署时设置的访问密码继续。</p><label>访问密码<input type="password" value={password} onChange={e=>setPassword(e.target.value)} required autoComplete="current-password" placeholder="输入访问密码"/></label>{error&&<div role="alert" className="alert danger">{error}</div>}<button className="primary" disabled={busy}>{busy?'正在连接…':'进入工作台'}<ArrowRight size={17}/></button><p className="small muted"><ShieldCheck size={15}/>策略审批与模拟成交由你确认</p></form></div>;
  const current=pages.find(p=>p.id===page)!;
  const account=mode==='historical'?undefined:state?.accounts.find(a=>a.id===`paper-${mode}-${currency}`);
  const visibleOrders=state?.orders.filter(o=>o.data_mode===mode&&o.account_id===`paper-${mode}-${currency}`)||[];
  const pending=visibleOrders.filter(o=>o.status==='pending');
  const reportResult=report?.result;
  const descriptions:Record<Page,string>={overview:'让想法经得起验证，让每一次决策有据可循。',autoresearch:'设定目标与风险边界，让模型逐轮提出假设、验证策略，保留每次实验的证据。',assets:'维护研究范围与交易单位，标的始终由你选择。',signals:'核验资讯利好、利空、中性判断及数值特征，记录来源与首次可用时间；不自动生成交易意图。',strategies:'从策略假设到独立版本，用证据决定下一步。',backtests:'在可复现的条件下检验策略，比较收益与风险。',trading:'查看模拟策略决策、人工订单及共享账户的已完成日线估值；非实时行情或单策略收益。',sources:'核验行情快照与消息来源，让研究有可靠的数据基础。',models:'让模型独立探索候选，保留分歧，由你评估与批准。',jobs:'跟踪任务进度、复核结果，保留每次实验的完整记录。',settings:'配置风险边界与研究计划，让自动化保持可控。',help:'查阅操作步骤、字段注释、研究方法与可复制示例。'};
  return <div className="app-shell"><a className="skip-link" href="#workspace-main">跳到主要内容</a><aside className="sidebar"><div className="brand"><div className="brand-symbol"><Compass size={23}/></div><div>Northstar<small>量化研究工作台</small></div></div><div className="workspace-label">个人工作空间 <span>单用户</span></div><nav aria-label="主要导航">{pages.map(p=><button key={p.id} aria-label={p.id==='autoresearch'?'Auto Research · 策略自主研究':p.name} title={p.id==='autoresearch'?'Auto Research · 策略自主研究':p.name} aria-current={page===p.id?'page':undefined} className={[page===p.id?'active':'',p.id==='sources'?'nav-divider':'',p.id==='autoresearch'?'nav-autoresearch':''].filter(Boolean).join(' ')} onClick={()=>setPage(p.id)}><p.icon size={18}/><span className="nav-name">{p.name}</span>{p.id==='autoresearch'&&<span className="nav-core">核心</span>}{p.id==='jobs'&&!!state?.jobs.filter(j=>j.status==='running').length&&<i className="nav-dot"/>}</button>)}</nav><div className="sidebar-safety"><ShieldCheck size={17}/><div>决策权，始终在你<small>仅研究与模拟交易</small></div></div><div className="sidebar-foot"><div className="account-avatar">P</div><div>个人投资者<small>独立研究工作空间</small></div><button className="icon" aria-label="退出登录" onClick={logout}><LogOut size={17}/></button></div></aside>
  <div className="main-shell"><header className="topbar"><div className="breadcrumb"><Compass size={15}/>工作空间 <ChevronRight size={14}/><strong>{current.name}</strong></div><div className="top-actions"><span className={'connection '+(connected?'online':'')} role="status"><i/>{connected?'状态已同步':'正在重连'}</span><ThemeToggle/><button className="icon" aria-label="刷新数据" title="刷新数据" disabled={busy} onClick={()=>{setMarketRefresh(value=>value+1);void act(refresh,'工作台状态已刷新；指数更新状态见市场观察')}}><RefreshCw size={17} className={busy?'is-spinning':''}/></button><button className="icon" aria-label={`查看最近加载的待确认事项，${pending.length} 笔订单`} title="查看待确认事项" onClick={()=>setPage('trading')}><Bell size={18}/>{pending.length>0&&<b className="notification"/>}</button><button className="icon compact-logout" aria-label="退出登录" onClick={logout}><LogOut size={17}/></button><div className="avatar" aria-label="个人账户">P</div></div></header>
  <main id="workspace-main" tabIndex={-1}><div className="page-heading"><div><h1>{current.name}</h1><p>{descriptions[page]}</p></div><div className="heading-actions"><select aria-label="数据环境" title="自动记住此浏览器的选择；未设置时默认历史数据回放" value={mode} onChange={e=>setMode(e.target.value)}><option value="historical">历史数据回放</option><option value="demo">演示数据</option><option value="live">真实市场数据</option></select>{page==='backtests'&&<button className="primary" disabled={busy||!state?.assets.length} title={!state?.assets.length?'请先添加投资标的':undefined} onClick={()=>openRun('backtest')}><Plus size={16}/>新建回测</button>}</div></div>
  <div className={'environment '+(mode==='demo'?'':'real')}><ShieldCheck size={17}/><span>{mode==='demo'?'演示环境 · 行情为可复现合成数据，收益不代表真实投资表现。':mode==='historical'?'历史回放 · 使用配置的历史快照；不可用于在线账户下单。':'真实数据环境 · 需自行导入并核验行情快照；交易仍为模拟交易。'}</span><b>实盘未启用</b></div>
  {error&&<div role="alert" className="alert danger">{error}<button className="icon" aria-label="关闭错误" onClick={()=>setError('')}><X size={16}/></button></div>}
  {notice&&<div role="status" className="toast"><Check size={16}/>{notice}</div>}
  {page==='help'?<HelpWiki navigate={setPage}/>:!state?<div className="panel"><Empty>正在连接后端服务…</Empty></div>:<>
  {state.risk.halted&&<div className="alert danger"><Square size={16}/>紧急停止已启用，新增模拟成交和交易意图被阻止。</div>}
  {page==='overview'&&<Dashboard state={state} account={account} mode={mode} currency={currency} markets={markets} marketLoading={marketLoading} marketError={marketError} connected={connected} onOpenJob={openReport} onCurrency={setCurrency} onNavigate={navigateFromMonitor} onAddAsset={()=>openAsset(null)} recentJobs={<JobTable jobs={state.jobs.slice(0,5)} onOpen={openReport}/>}/>}
  {page==='assets'&&<section className="panel"><div className="panel-heading"><h3>我的标的池 <span className="count">{state.assets.length}</span></h3><div className="row"><button onClick={()=>download('assets.json',state.assets)}><ArrowDownToLine size={15}/>导出</button><label className="button-label" role="button" tabIndex={0} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.currentTarget.querySelector('input')?.click()}}}>导入 JSON<input hidden type="file" accept=".json" onChange={e=>{const file=e.target.files?.[0];if(file)act(async()=>{const rows=JSON.parse(await file.text());if(!Array.isArray(rows)||rows.length>100)throw new Error('导入须为最多100条的JSON数组');await api('/assets/import',{assets:rows});},'导入完成；有冲突时整批拒绝，不覆盖已有标的');e.target.value=''}}/></label><button className="primary" onClick={()=>openAsset(null)}><Plus size={15}/>添加标的</button></div></div>{!state.assets.length?<Empty>尚无标的。手动添加股票或 ETF 后即可开始回测。</Empty>:<div className="table-wrap"><table><thead><tr><th>标的名称 / 代码</th><th>市场</th><th>币种</th><th>持仓数量</th><th>持仓市值 ↓</th><th>估值收盘价 / 日期</th><th>每手数量</th><th>标签</th><th>状态</th><th>操作</th></tr></thead><tbody>{sortAssetsByValue(state.assets,valuations).map(a=><tr key={a.id}><td><strong>{a.name}</strong><small className="mono">{a.symbol}</small></td><td>{{CN:'A股',HK:'港股',US:'美股'}[a.market]}</td><td>{a.currency}</td><td>{a.holding_quantity??0}</td><td>{holdingValue(a,valuations.find(q=>q.id===a.id))===undefined?'无法估值':money(holdingValue(a,valuations.find(q=>q.id===a.id)))}<small>{a.currency}</small></td><td>{money(valuations.find(q=>q.id===a.id)?.price)}<small title={valuations.find(q=>q.id===a.id)?.source}>{valuations.find(q=>q.id===a.id)?.quote_date||'无本地未复权行情'}</small></td><td>{a.lot}</td><td>{a.tags.join(' / ')||'—'}</td><td><span className={'status-dot '+(a.enabled?'on':'')}/>{a.enabled?'启用':'停用'}</td><td><div className="row"><button className="text-button" disabled={busy} onClick={()=>openAsset(a)}>编辑</button><button className="text-button" disabled={busy} onClick={()=>act(()=>api('/assets/'+a.id,{...a,enabled:!a.enabled},'PUT'))}>{a.enabled?'停用':'启用'}</button></div></td></tr>)}</tbody></table></div>}<div className="panel-note">按币种分组，组内持仓市值降序；无法估值的标的置后。市值＝手工持仓数量 × 本地未复权收盘价，非实时估值，不混用模拟仓位或演示行情。请核对报价日期；缺少行情时需先准备未复权行情缓存。{valuationError&&<span role="alert"> 估值读取失败：{valuationError}</span>}</div><div className="panel-note">投资代码建议使用市场前缀，例如 CN.510300、HK.00700、US.SPY。每手数量由你核验后填写；代码、市场与每手数量创建后不可修改。</div></section>}
  {page==='signals'&&<SignalLibrary assets={state.assets} news={news} newsError={sourceFailures}/>}
  {page==='autoresearch'&&<OptimizationWorkbench state={state} mode={mode} onModeChange={setMode} busy={busy} onOpen={openReport} submitted={id=>{openReport(id);refresh().catch(e=>setError(e.message));setNotice('策略自主研究已提交，达到目标或预算上限后停止')}}/>}
  {page==='strategies'&&<><StrategyLibrary templates={state.strategy_templates||[]} busy={busy} added={templateAdded}/><div className="section-row"><h3>策略版本 <span className="subtle">已保存版本不可原地修改</span></h3><div className="row"><a className="text-button" href="#help/strategy-params"><BookOpen size={15}/>参数说明与计算公式</a><button className="primary" onClick={()=>openStrategy(null)}><Plus size={16}/>新建策略</button></div></div><div className="panel-note">批准策略（原“批准发布”）仅允许该版本用于后续模拟信号；须先完成该版本的回测或候选评估，不代表收益达标，也不会自动切换计划、运行或下单。撤销批准将阻止新增信号及待确认订单成交，不自动平仓，不删除版本或历史记录；仍可回测。</div><div className="strategy-grid">{state.strategies.map(s=><article key={s.id} className="panel strategy-card"><div className="row between"><div className="strategy-icon"><Code2 size={23}/></div><Badge value={s.status}/></div><h3>{s.name}</h3><p>{s.notes||'暂无版本说明'}</p><div className="strategy-params">{Object.entries(s.params).map(([k,v])=><span key={k}>{k}<b>{v}</b></span>)}</div><div className="small muted">{time(s.created_at)}<br/>版本 {s.id.slice(0,8)}{s.parent_id&&` · 父版本 ${s.parent_id.slice(0,8)}`}</div><footer><button onClick={()=>openStrategy(s)}>查看 / 派生版本</button><StrategyApprovalButton strategyId={s.id} status={s.status} busy={busy} allowRevoke {...approval}/></footer></article>)}</div><div className="panel-note">Python 支持局部赋值、if/return、切片与 mean/min/max/sum/len/abs，以及 ema/rsi/kama/realized_vol/efficiency_ratio/zscore/channel_signal 纯指标（周期上限2000、历史上限8192）。导入、循环、网络、文件访问和任意 Python 执行均被拒绝。</div></>}
  {page==='backtests'&&<section className="panel"><div className="panel-heading"><h3>实验记录</h3><span className="muted small">点开记录查看净值、成交与可复现快照</span></div><JobTable jobs={state.jobs.filter(j=>j.kind==='backtest'||j.kind==='research'||j.kind==='optimization')} onOpen={openReport}/><div className="panel-note">回测为单标的、单币种。收盘信号在下一交易日开盘撮合，含可配置费用与滑点。收益不代表未来表现。</div></section>}
  {page==='trading'&&<><PaperTradingOverview mode={mode} currency={currency} serverTime={state.server_time} onCurrency={setCurrency} onOpenJob={openReport} onRunSignal={()=>openRun('signal')} canRun={!!state.assets.length}/>{mode!=='historical'&&<section className="panel paper-index-observer"><div className="paper-section-heading"><div><h2>指数观察 · 不参与账户估值</h2><p>合成演示指数或可能延迟的公开指数报价；不是持仓标的实时价格，也不用于模拟成交。</p></div><span className="tag">{mode==='demo'?'合成行情':'延迟报价'}</span></div>{marketLoading&&<p className="paper-placeholder" role="status">正在更新指数观察报价…</p>}{marketError&&<p className="paper-error" role="alert">指数观察更新失败：{marketError}。旧报价不代表当前行情。</p>}{markets.length ? <div className="paper-index-list">{markets.map(item=><div key={item.symbol}><strong>{item.name}</strong><b>{money(item.price)}</b><small>{item.quoted_at||item.date||'报价日期未知'} · {item.synthetic?'合成演示数据':item.source||'来源未知'}{item.stale?' · 旧报价':''}</small>{item.warning&&<small role="status">{item.warning}</small>}{item.error&&<small role="alert">{item.error}</small>}</div>)}</div>:<p className="paper-placeholder">{marketLoading?'正在读取指数观察报价…':'暂无指数报价；不影响持仓估值。'}</p>}</section>}<div className="section-row"><h3>模拟交易 <span className="subtle">实盘未接入，所有订单仅改变模拟账户</span></h3><div className="row"><button className="danger-button" disabled={busy} onClick={()=>act(()=>api('/settings/risk',{...state.risk,halted:!state.risk.halted},'PUT'))}><Square size={14}/>{state.risk.halted?'解除紧急停止':'紧急停止'}</button></div></div><section className="panel"><div className="panel-heading"><h3>订单审批与成交 <span className="count">最近加载的记录</span></h3><p className="small muted">仅人工确认后才更新模拟账户；此表仅显示最近100条订单中当前账户的记录，完整累计数见上方决策区。</p><span className="tag">PAPER ONLY</span></div>{!visibleOrders.length?<Empty>运行已批准策略，生成待确认模拟订单</Empty>:<div className="table-wrap"><table><thead><tr><th>标的 / 账户</th><th>方向</th><th>数量</th><th>参考价</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{visibleOrders.map(o=><tr key={o.id}><td><strong>{o.details.symbol}</strong><small>{o.account_id}</small></td><td className={o.side==='buy'?'positive':'negative'}>{o.side==='buy'?'买入':'卖出'}</td><td>{o.quantity}</td><td>{money(o.details.fill_price||o.price)}<small>报价日 {o.details.quote_date}</small></td><td><Badge value={o.status}/></td><td>{time(o.created_at)}</td><td>{o.status==='pending'?<div className="row"><button className="primary small" disabled={busy} onClick={()=>{if(window.confirm(`确认模拟${o.side==='buy'?'买入':'卖出'} ${o.details.symbol} ${o.quantity} 股？\n参考价 ${o.price}，成交另计配置的滑点与费用。\n账户：${o.account_id}`))act(()=>api('/orders/'+o.id+'/confirm',{}),'模拟订单已成交')}}>确认</button><button disabled={busy} onClick={()=>act(()=>api('/orders/'+o.id+'/cancel',{}))}>取消</button></div>:'—'}</td></tr>)}</tbody></table></div>}</section></>}
  {(page==='sources'||page==='models')&&<><div className="section-row"><h3>{page==='models'?'研究模型':'行情与新闻接口'} <span className="subtle">运行时冻结配置，修改只影响新任务</span></h3><button className="primary" onClick={()=>openProvider(null)}><Plus size={15}/>添加接口</button></div><div className="provider-grid">{state.providers.filter(p=>page==='models'?p.kind==='model':p.kind!=='model').map(p=><article className="panel provider-card" key={p.id}><div className="row between"><div className="provider-icon">{p.kind==='model'?<Sparkles/>:p.kind==='news'?<BookOpen/>:<Database/>}</div><span className={'tag '+(p.enabled?'green':'')}>{p.enabled?'已启用':'已停用'}</span></div><h3>{p.name}</h3><p className="mono">{p.driver}</p><div className="provider-details"><span>优先级<b>{p.priority}</b></span><span>运行能力<b>{p.driver.startsWith('demo')?'演示适配器':p.driver==='openai_compatible'?'HTTPS 模型调用':p.driver==='akshare'?'历史日线 + Parquet 缓存':p.driver==='yahoo'?'外部历史日线（非实时）':'本地文件接入'}</b></span></div><footer><button onClick={()=>openProvider(p)}>配置接口</button><button className="text-button" disabled={busy} onClick={()=>act(()=>api('/providers/'+p.id,{...p,enabled:!p.enabled},'PUT'))}>{p.enabled?'停用':'启用'}</button></footer></article>)}</div>{page==='models'?<div className="panel top-gap"><div className="panel-heading"><h3>多模型研究工作流</h3><button className="primary" disabled={!state.assets.length} onClick={()=>openRun('research')}><Sparkles size={16}/>开始研究</button></div><div className="workflow-description"><p>选择多个模型 → 独立生成候选 → 训练集筛选 → 样本外评估 → 人工审批</p><p className="muted">{state.capabilities.model_budget}。演示研究器不调用外部大模型；实际模型用量与候选信息记录在研究报告中。</p><p className="muted">模型凭据从后端环境变量读取。前端只保存变量名称，不保存或展示明文密钥。</p></div></div>:<section className="panel top-gap"><div className="panel-heading"><h3>政策与新闻聚合</h3><span className="muted small">按首次可获取时间过滤 · 保留原始来源</span></div>{sourceFailures&&<div className="alert danger">{sourceFailures}</div>}{news.length?news.map((n,i)=><article className="news-item" key={i}><span className="tag">{n.synthetic?'演示消息':n.provider}</span><h3>{n.title}</h3><p>{n.body}</p><small>{time(n.published_at)} · 首次可获取 {time(n.first_seen_at)}</small>{/^https?:\/\//.test(n.url)&&<a href={n.url} target="_blank" rel="noreferrer">查看原文 ↗</a>}</article>):<Empty>暂无时间与来源信息完整的消息</Empty>}</section>}</>}
  {page==='jobs'&&<section className="panel"><div className="panel-heading"><h3>任务历史 <span className="count">{state.jobs.length}</span></h3><span className="muted small">最近80条 · 后端保存完整记录</span></div><JobTable jobs={state.jobs} onOpen={openReport} actions={j=><div className="row">{['queued','running'].includes(j.status)&&<button disabled={busy} onClick={()=>act(()=>api('/jobs/'+j.id+'/cancel',{}))}>取消</button>}{['failed','cancelled'].includes(j.status)&&j.kind!=='signal'&&j.kind!=='optimization'&&<button disabled={busy} onClick={()=>act(()=>api('/jobs/'+j.id+'/retry',{}),'重试已排队')}>重试</button>}</div>}/></section>}
  {page==='settings'&&<SettingsPanel state={state} action={formAction} busy={busy}/>}
  </>}
  <footer className="main-footer"><span>Northstar / Personal Quant Workspace</span><span>策略自主迭代 · 投资由你决策 <span className="dot">·</span> v0.1.0</span></footer></main></div>
  {modal==='asset'&&<AssetForm asset={selectedAsset} close={()=>{setModal(null);setSelectedAsset(null)}} busy={busy} action={formAction}/>}
  {modal==='strategy'&&<StrategyForm strategy={selectedStrategy} close={()=>setModal(null)} busy={busy} action={formAction}/>}
  {modal==='provider'&&<ProviderForm provider={selectedProvider} modelDefault={page==='models'} close={()=>setModal(null)} busy={busy} action={formAction}/>}
  {modal==='run'&&state&&<RunForm state={state} kind={runKind} mode={mode} strategyId={runStrategyId} close={()=>setModal(null)} busy={busy} action={formAction} submitted={id=>{setModal(null);openReport(id)}}/>}
  {reportId&&<Modal title="任务详情与研究报告" wide close={()=>setReportId('')}><div className="modal-body">{!report?<Empty>正在读取任务…</Empty>:<><div className="report-toolbar"><div className="row"><Badge value={report.kind}/><Badge value={report.status}/><span className="small muted">{time(report.created_at)}</span></div><div className="report-export-actions"><button disabled={report.kind==='optimization'&&!researchManifest(report)} onClick={()=>download('manifest-'+report.id+'.json',report.kind==='optimization'?researchManifest(report):report.payload)}>{report.kind==='optimization'?'导出最终策略清单':'导出运行清单'}</button><button onClick={()=>download('report-'+report.id+'.json',report)}>导出报告</button></div></div><BacktestProgressPanel job={report}/>{report.error&&<div className="alert danger top-gap">{report.error}</div>}{(report.kind==='optimization'||report.kind==='research')&&<ResearchTaskContext job={report}/>} {report.kind==='optimization'&&<OptimizationReport job={report} strategies={state?.strategies||[]} approval={approval} busy={busy} action={formAction}/>} {reportResult?.metrics?<><div className="report-title"><h2>{reportResult.symbol} <Badge value={reportResult.data_mode}/></h2><span>{reportResult.source.name} · {reportResult.currency}</span></div><MetricsGrid metrics={reportResult.metrics}/>{reportResult.strategy_study&&<StrategyStudyReport study={reportResult.strategy_study}/>}<SignalDiagnosis report={reportResult}/><ReportChart report={reportResult}/>{report.kind!=='optimization'&&<ExperimentRevision key={report.id} job={report} approval={approval} strategies={state?.strategies||[]} busy={busy} onOpen={openReport} submitted={id=>{openReport(id);refresh().catch(e=>setError(e.message));setNotice('新策略版本已保存，同条件复测已提交')}} action={formAction}/>}{reportResult.intent&&<div className="alert">{reportResult.intent.message}</div>}{reportResult.research&&<section className="research-result"><h3>模型研究与独立评估</h3><p>{reportResult.research.note}</p><div className="research-selected"><h3>{reportResult.research.selected.name}</h3><NamingSource source={reportResult.research.selected.naming_source}/><p className="small muted">按训练集夏普选定；样本外评估不用于选择候选。</p></div>{reportResult.research.candidates.map((candidate,index)=><details className="optimization-round" key={index}><summary><span className="research-round-heading"><strong>{candidate.name}</strong><small>{candidate.provider_name}</small></span></summary><NamingSource source={candidate.naming_source}/><StrategyExplanation strategy={candidate}/><StrategyDiff change={candidate.change} parentLabel="起始策略"/><details><summary>完整候选代码与参数（只读）</summary><pre>{candidate.code}</pre><pre>{JSON.stringify(candidate.params,null,2)}</pre></details></details>)}<div className="table-wrap"><table><thead><tr><th>模型候选</th><th>训练集收益</th><th>训练集夏普</th><th>说明</th></tr></thead><tbody>{reportResult.research.candidates.map((c,i)=><tr key={i}><td>{c.provider_name}</td><td>{pct(c.training_metrics.total_return)}</td><td>{c.training_metrics.sharpe.toFixed(2)}</td><td>{c.notes}</td></tr>)}</tbody></table><table><thead><tr><th>样本外 · 从 {reportResult.research.holdout_start}</th><th>收益率</th><th>最大回撤</th><th>夏普</th></tr></thead><tbody>{[['当前版本',reportResult.research.holdout_baseline],['候选版本',reportResult.research.holdout_candidate]].map(([name,m])=>{const metrics=m as Metrics;return <tr key={name as string}><td>{name as string}</td><td>{pct(metrics.total_return)}</td><td>{pct(metrics.max_drawdown)}</td><td>{metrics.sharpe.toFixed(2)}</td></tr>})}</tbody></table></div><p>候选版本：<code>{reportResult.research.candidate_id}</code></p><StrategyApprovalButton strategyId={reportResult.research.candidate_id} status={state?.strategies.find(s=>s.id===reportResult.research!.candidate_id)?.status} busy={busy} label="人工批准候选策略" {...approval}/>{reportResult.research.failures.length>0&&<pre>{JSON.stringify(reportResult.research.failures,null,2)}</pre>}</section>}<details><summary>成交明细 · {reportResult.trades.length} 笔</summary><div className="table-wrap"><table><thead><tr><th>信号日</th><th>成交日</th><th>方向</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>{reportResult.trades.map((t,i)=><tr key={i}><td>{t.signal_date}</td><td>{t.date}</td><td className={t.side==='buy'?'positive':t.side==='sell'?'negative':undefined}><strong>{t.side==='buy'?'▲ 买入':t.side==='sell'?'▼ 卖出':t.side}</strong></td><td>{t.quantity}</td><td>{money(t.price)}</td><td>{money(t.fee)}</td></tr>)}</tbody></table></div></details><p className="small muted">{reportResult.methodology}</p>{reportResult.warnings.map(w=><p className="small muted" key={w}>{w}</p>)}<details><summary>数据快照与来源</summary><pre>{JSON.stringify({hash:reportResult.data_hash,snapshot:reportResult.snapshot,source:reportResult.source},null,2)}</pre></details></>:!report.error&&<Empty>{report.status==='running'?'后台正在计算，页面会自动更新。':report.status==='queued'?'任务已排队，请确认 Worker 已启动。':'此任务没有计算报告。'}</Empty>}</>}</div></Modal>}
  </div>
}

function JobTable({jobs,onOpen,actions}:{jobs:Job[];onOpen:(id:string)=>void;actions?:(j:Job)=>ReactNode}){return jobs.length?<div className="table-wrap"><table><thead><tr><th>任务 / 标的</th><th>类型</th><th>状态</th><th>创建时间</th><th>收益率</th><th>操作</th></tr></thead><tbody>{jobs.map(j=><tr key={j.id}><td><strong>{j.summary?.symbol||String(j.spec?.asset_id||'').slice(0,8)||'等待执行'}</strong><small className="mono">{j.id.slice(0,10)}</small></td><td><Badge value={j.kind}/></td><td><Badge value={j.status}/></td><td>{time(j.created_at)}</td><td className={(j.summary?.metrics?.total_return||0)>=0?'positive':'negative'}>{pct(j.summary?.metrics?.total_return)}</td><td><div className="row"><button className="text-button" onClick={()=>onOpen(j.id)}>查看详情</button>{actions?.(j)}</div></td></tr>)}</tbody></table></div>:<Empty>暂无任务记录，创建回测开始验证策略</Empty>}

type FormProps={close:()=>void;busy:boolean;action:(fn:()=>Promise<unknown>,success?:string)=>Promise<void>};
function AssetForm({asset,close,busy,action}:FormProps&{asset:Asset|null}){
  const [market,setMarket]=useState(asset?.market||'CN');
  const dismiss=()=>{if(!busy)close()};
  return <Modal title={asset?'编辑投资标的':'添加投资标的'} close={dismiss}>
    <form className="form" onSubmit={e=>{
      e.preventDefault();
      if(busy)return;
      const f=new FormData(e.currentTarget);
      action(async()=>{
        await api('/assets'+(asset?'/'+asset.id:''),{
          symbol:asset?.symbol??f.get('symbol'),
          name:String(f.get('name')).trim(),
          market:asset?.market??market,
          lot:asset?.lot??Number(f.get('lot')),
          holding_quantity:Number(f.get('holding_quantity')),
          tags:String(f.get('tags')).split(',').map(s=>s.trim()).filter(Boolean),
          enabled:f.has('enabled'),
        },asset?'PUT':'POST');
        close();
      },asset?'标的信息已更新':'标的已加入自选池');
    }}>
      <label>市场<select value={market} disabled={!!asset||busy} onChange={e=>setMarket(e.target.value as Asset['market'])}>
        <option value="CN">A股 / 场内 ETF</option><option value="HK">港股 / 场内 ETF</option><option value="US">美股 / 场内 ETF</option>
      </select></label>
      <div className="form-grid">
        <label>标的代码<input name="symbol" defaultValue={asset?.symbol} readOnly={!!asset} disabled={busy}
          placeholder={market+'.'+(market==='CN'?'510300':market==='HK'?'00700':'SPY')}
          required pattern="[A-Za-z0-9_.-]+" maxLength={32}/></label>
        <label>显示名称<input name="name" defaultValue={asset?.name} disabled={busy} required
          maxLength={80} pattern=".*\S.*" placeholder="输入标的名称"/></label>
      </div>
      <label>每手数量<input name="lot" type="number" min={1} max={100000} key={market}
        defaultValue={asset?.lot??(market==='CN'?100:1)} readOnly={!!asset} disabled={busy} required/>
        <small>{asset?'代码、市场和每手数量创建后不可修改。':'请核验该标的真实交易单位，特别是港股。'}</small>
      </label>
      <label>持仓数量<input name="holding_quantity" type="number" min={0} max={2147483647} step={1}
        defaultValue={asset?.holding_quantity??0} disabled={busy} required/>
        <small>独立记录你的持股数量，不改变模拟账户现金、仓位或估值；与每手交易单位无关。</small>
      </label>
      <label>标签<input name="tags" defaultValue={asset?.tags.join(', ')} disabled={busy} placeholder="ETF, 长期观察"/>
        <small>多个标签用英文逗号分隔，最多20个。</small>
      </label>
      <label className="checkbox"><input name="enabled" type="checkbox" defaultChecked={asset?.enabled??true}
        disabled={busy}/>启用标的</label>
      <footer><button type="button" onClick={dismiss} disabled={busy}>取消</button>
        <button className="primary" disabled={busy}>{busy?'保存中…':asset?'保存修改':'添加标的'}</button>
      </footer>
    </form>
  </Modal>;
}
function StrategyForm({strategy,close,busy,action}:FormProps&{strategy:Strategy|null}){const [code,setCode]=useState(strategy?.code||'def target(history, params):\n    # history只含截至当前信号日的收盘价；不足20根时保持空仓。\n    if len(history) < 20:\n        return 0.0\n    # 当前价严格高于含当日的20日均线时参与。\n    if history[-1] > mean(history[-20:]):\n        # weight=0.15表示15%目标仓位，不是股数；平台风控仍生效。\n        return params["weight"]\n    return 0.0\n');return <Modal title={strategy?'查看策略 / 创建新版本':'新建 Python 策略'} wide close={close}><form className="form" onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);action(async()=>{await api('/strategies',{name:f.get('name'),code,params:JSON.parse(String(f.get('params'))),notes:f.get('notes'),parent_id:strategy?.id||null});close()},'新版本已保存，等待回测和批准')}}><div className="form-grid"><label>新版本名称<input name="name" required maxLength={80} defaultValue={strategy?strategy.name+' · 新版本':'趋势策略'}/></label><label>参数 JSON<input className="mono" name="params" defaultValue={JSON.stringify(strategy?.params||{weight:.15})} required/></label></div><div className={strategy?'code-comparison':''}>{strategy&&<label>已保存版本（只读）<pre className="code-editor original">{strategy.code}</pre></label>}<label>{strategy?'新版本代码':'策略代码'}<textarea className="code-editor" spellCheck={false} value={code} onChange={e=>setCode(e.target.value)} rows={14} required/></label></div><label>变更说明<textarea name="notes" placeholder="描述交易逻辑与这次调整的原因" maxLength={2000}/></label><p className="small muted">返回值为0–1的目标仓位，随后还会受平台风险上限限制。使用 # 整行注释说明预热、参数单位与信号条件；不要添加文档字符串、类型注解或导入。完整规范见帮助 Wiki「策略代码规范与注释要求」。保存即校验。</p><footer><button type="button" onClick={close}>关闭</button><button className="primary" disabled={busy}>保存为新版本</button></footer></form></Modal>}
function ProviderForm({provider,modelDefault,close,busy,action}:FormProps&{provider:Provider|null;modelDefault:boolean}){const [driver,setDriver]=useState(provider?.driver||(modelDefault?'openai_compatible':'akshare'));const defaults:Record<string,unknown>={demo:{},akshare:{directory:'cache/akshare',mode:'historical',price_basis:'raw',adjust:'none',cache_hours:'24',offline:'false'},parquet:{directory:'imports',mode:'historical',price_basis:'raw'},yahoo:{mode:'historical',price_basis:'adjusted'},demo_model:{},openai_compatible:{base_url:'https://your-provider.example/v1',model:'model-name',key_env:'MODEL_KEY_PRIMARY'},demo_news:{},json_news:{file:'imports/news.json'}};const [config,setConfig]=useState(JSON.stringify(provider?.config||defaults[driver],null,2));return <Modal title={provider?'配置接口':'添加可插拔接口'} close={close}><form className="form" onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);action(async()=>{await api('/providers'+(provider?'/'+provider.id:''),{name:f.get('name'),driver,kind:['demo','parquet','yahoo','akshare'].includes(driver)?'market':['demo_model','openai_compatible'].includes(driver)?'model':'news',config:JSON.parse(config),priority:Number(f.get('priority')),enabled:provider?.enabled??true},provider?'PUT':'POST');close()})}}><label>接口名称<input name="name" defaultValue={provider?.name} required maxLength={80}/></label><div className="form-grid"><label>驱动<select value={driver} onChange={e=>{setDriver(e.target.value);setConfig(JSON.stringify(defaults[e.target.value],null,2))}}>{Object.keys(defaults).map(k=><option key={k}>{k}</option>)}</select></label><label>优先级（越小越优先）<input name="priority" type="number" min={0} max={1000} defaultValue={provider?.priority||10}/></label></div><label>驱动配置 JSON<textarea className="code-editor" value={config} onChange={e=>setConfig(e.target.value)} rows={8}/></label><p className="small muted">{driver==='akshare'?'AKShare 历史日线自动保存到 DATA_DIR 内 Parquet 缓存；cache_hours 是字符串小时数（默认24，0为每次刷新），offline 设为 "true" 可离线回放已有缓存。默认不复权（raw/none），回测不计分红拆股；可改为 adjusted/qfq，但复权因子不可用会报错。只获取当地昨日及之前日线。回测请选择“历史数据回放”并新建任务；抓取失败不使用过期缓存或演示数据。':driver==='yahoo'?'Yahoo 自动获取历史复权日线，无需密钥；回测请选择“历史数据回放”并新建任务。公开接口可能限流，失败时可改用 Parquet 快照；不提供实时交易报价。':'文件路径相对于后端 DATA_DIR。模型只保存密钥环境变量名称；HTTPS 地址需加入 MODEL_ALLOWED_BASE_URLS。DeepSeek 官方使用 https://api.deepseek.com、model: deepseek-flash、api_format: chat_completions；密钥仅放在后端 .env.models 的 MODEL_KEY_DEEPSEEK，不要粘贴到此配置。'}</p><footer><button type="button" onClick={close}>取消</button><button className="primary" disabled={busy}>保存配置</button></footer></form></Modal>}
function RunForm({state,kind,mode,strategyId,close,busy,action,submitted}:FormProps&{state:State;kind:string;mode:string;strategyId?:string;submitted:(id:string)=>void}){const [models,setModels]=useState<string[]>(state.providers.filter(p=>p.enabled&&p.kind==='model').slice(0,1).map(p=>p.id));return <Modal title={kind==='research'?'发起多模型研究':kind==='signal'?'运行策略 / 生成模拟订单':'新建回测实验'} close={close}><form className="form" onSubmit={e=>{e.preventDefault();const f=new FormData(e.currentTarget);action(async()=>{const r=await api<{id:string}>('/jobs',{kind,mode,strategy_id:f.get('strategy'),asset_id:f.get('asset'),start:f.get('start'),end:f.get('end'),initial:Number(f.get('initial')),fee_bps:Number(f.get('fee')),slippage_bps:Number(f.get('slippage')),model_ids:kind==='research'?models:[],client_key:createClientKey()});submitted(r.id)},'任务已提交后台')}}><div className="alert"><Badge value={mode}/><span>{kind==='signal'?'将生成待人工确认的模拟订单。':'数据和策略配置在提交时冻结。'}</span></div><label>投资标的<select name="asset" required>{state.assets.filter(a=>a.enabled).map(a=><option value={a.id} key={a.id}>{a.name} / {a.symbol} / {a.currency}</option>)}</select></label><label>策略版本<select name="strategy" defaultValue={strategyId||undefined} required>{state.strategies.filter(s=>kind!=='signal'||s.status==='approved').map(s=><option value={s.id} key={s.id}>{s.name} · {s.id.slice(0,6)}</option>)}</select></label><div className="form-grid"><label>开始日期<input name="start" type="date" min="2015-01-01" defaultValue={strategyId?new Date(Date.now()-3*365*86400000).toISOString().slice(0,10):yearAgo()} required/></label><label>结束日期<input name="end" type="date" defaultValue={today()} max={today()} readOnly={kind==='signal'} required/></label></div><div className="form-grid three"><label>初始资金<input name="initial" type="number" min={1} max={1e10} defaultValue={100000} required/></label><label>费用 / bps<input name="fee" type="number" min={0} max={500} step="0.1" defaultValue={5} required/></label><label>滑点 / bps<input name="slippage" type="number" min={0} max={500} step="0.1" defaultValue={5} required/></label></div>{kind==='research'&&<fieldset><legend>协作模型（最多4个）</legend>{state.providers.filter(p=>p.kind==='model'&&p.enabled).map(p=><label className="checkbox" key={p.id}><input type="checkbox" checked={models.includes(p.id)} onChange={e=>setModels(e.target.checked?[...models,p.id]:models.filter(id=>id!==p.id))}/>{p.name}</label>)}</fieldset>}<footer><button type="button" onClick={close}>取消</button><button className="primary" disabled={busy||!state.assets.some(a=>a.enabled)||(kind==='research'&&(!models.length||models.length>4))}>提交任务 <ArrowRight size={15}/></button></footer></form></Modal>}
function SettingsPanel({state,action,busy}:{state:State;action:FormProps['action'];busy:boolean}){const [risk,setRisk]=useState<Risk>(state.risk),[schedule,setSchedule]=useState<Schedule>(state.schedule);useEffect(()=>{setRisk(r=>({...r,halted:state.risk.halted}))},[state.risk.halted]);return <div className="settings-grid"><section className="panel"><div className="panel-heading"><h3>风险控制</h3><ShieldCheck size={18}/></div><form className="form" onSubmit={e=>{e.preventDefault();action(()=>api('/settings/risk',risk,'PUT'))}}>{([{key:'max_weight',name:'单标的仓位上限（0–1）',min:.001,max:1,step:.01},{key:'max_total_weight',name:'总仓位上限（0–1）',min:.001,max:1,step:.01},{key:'max_order_value',name:'单笔金额上限（账户币种）',min:1,max:1e9,step:1},{key:'daily_loss_limit',name:'每日亏损阈值（0–1）',min:.001,max:1,step:.01},{key:'approval_ttl_minutes',name:'订单确认有效期（分钟）',min:1,max:60,step:1}] as const).map(f=><label key={f.key}>{f.name}<input type="number" min={f.min} max={f.max} step={f.step} value={risk[f.key]} required onChange={e=>setRisk({...risk,[f.key]:Number(e.target.value)})}/></label>)}<label className="checkbox"><input type="checkbox" checked={risk.halted} onChange={e=>setRisk({...risk,halted:e.target.checked})}/>紧急停止新增交易意图和成交</label><button className="primary" disabled={busy}>保存风险设置</button><p className="small muted">当前为模拟风控。持仓按记录报价估值，日亏损检查不能替代实盘持续盯市。</p></form></section><section className="panel"><div className="panel-heading"><h3>自动研究与信号</h3><Clock3 size={18}/></div><form className="form" onSubmit={e=>{e.preventDefault();action(()=>api('/settings/schedule',schedule,'PUT'))}}><label className="checkbox"><input type="checkbox" checked={schedule.weekly_enabled} onChange={e=>setSchedule({...schedule,weekly_enabled:e.target.checked})}/>每周六 12:00（北京）发起研究</label><label className="checkbox"><input type="checkbox" checked={schedule.daily_enabled} onChange={e=>setSchedule({...schedule,daily_enabled:e.target.checked})}/>每天 09:00（北京）生成模拟信号</label><label>研究 / 交易标的<select value={schedule.asset_id} onChange={e=>setSchedule({...schedule,asset_id:e.target.value})}><option value="">请选择</option>{state.assets.filter(a=>a.enabled).map(a=><option value={a.id} key={a.id}>{a.name} · {a.symbol}</option>)}</select></label><label>运行策略版本<select value={schedule.strategy_id} onChange={e=>setSchedule({...schedule,strategy_id:e.target.value})}><option value="">请选择</option>{state.strategies.map(s=><option value={s.id} key={s.id}>{s.name} · {s.status==='approved'?'已批准':'待批准'}</option>)}</select></label><label>数据环境<select value={schedule.mode} onChange={e=>setSchedule({...schedule,mode:e.target.value})}><option value="demo">演示数据</option><option value="live">真实市场数据</option></select></label><fieldset><legend>研究模型</legend>{state.providers.filter(p=>p.kind==='model'&&p.enabled).map(p=><label key={p.id} className="checkbox"><input type="checkbox" checked={schedule.model_ids.includes(p.id)} onChange={e=>setSchedule({...schedule,model_ids:e.target.checked?[...schedule.model_ids,p.id]:schedule.model_ids.filter(id=>id!==p.id)})}/>{p.name}</label>)}</fieldset><p className="small muted">首版调度固定一个标的与一个版本。模型研究不会自动更改运行版本；批准后在这里切换，下次任务生效。每天统一时间运行，不是交易所收盘事件调度。</p><button className="primary" disabled={busy}>保存任务计划</button></form></section></div>}
