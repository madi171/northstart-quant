import {useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {X, Inbox} from 'lucide-react';
import type {Point, Metrics} from './types';
export const pct = (x: number | undefined) => x === undefined ? '—' : (x * 100).toFixed(2) + '%';
export const money = (x: number | undefined) => x === undefined ? '—' : x.toLocaleString('zh-CN', {maximumFractionDigits: 2, minimumFractionDigits: 2});
export const time = (x: string | null | undefined) => x ? new Date(x).toLocaleString('zh-CN', {hour12:false}) : '—';
const labels: Record<string, string> = {queued:'排队中',running:'运行中',succeeded:'已完成',failed:'失败',cancelled:'已取消',candidate:'待评估 / 审批',approved:'已批准',pending:'待确认',filled:'已成交',backtest:'策略回测',research:'模型研究',optimization:'策略自主研究',signal:'交易信号',demo:'演示数据',historical:'历史回放',live:'真实数据'};
export function Badge({value}: {value:string}) {return <span className={'badge '+value}>{labels[value] || value}</span>}
export function Empty({children}: {children: ReactNode}) {return <div className="empty"><Inbox size={28}/><p>{children}</p></div>}
const modalStack: HTMLElement[] = [];
let previousBodyOverflow = '';

export function Modal({title, children, close, wide=false}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(close);
  const titleId = useId();
  useEffect(() => {closeRef.current = close;}, [close]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!modalStack.length) previousBodyOverflow = document.body.style.overflow;
    modalStack.push(dialog);
    document.body.style.overflow = 'hidden';
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
    )).filter(element => element.getClientRects().length > 0);
    const frame = requestAnimationFrame(() => (focusable()[0] || dialog).focus());
    const handleKey = (event: KeyboardEvent) => {
      if (modalStack[modalStack.length - 1] !== dialog || event.isComposing) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeRef.current();
      }
      if (event.key !== 'Tab') return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (!first || !last) {
        event.preventDefault();
        dialog.focus();
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog)) {
        event.preventDefault();
        first.focus();
      }
    };
    const containFocus = (event: FocusEvent) => {
      if (modalStack[modalStack.length - 1] === dialog && !dialog.contains(event.target as Node)) {
        (focusable()[0] || dialog).focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    document.addEventListener('focusin', containFocus);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('focusin', containFocus);
      const index = modalStack.indexOf(dialog);
      if (index !== -1) modalStack.splice(index, 1);
      if (!modalStack.length) document.body.style.overflow = previousBodyOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return <div className="overlay" onClick={close}>
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
      className={'modal ' + (wide ? 'wide' : '')} onClick={event => event.stopPropagation()}>
      <header><h2 id={titleId}>{title}</h2><button type="button" className="icon" aria-label="关闭" onClick={close}><X size={20}/></button></header>
      {children}
    </section>
  </div>;
}
export function MetricsGrid({metrics:m}: {metrics:Metrics}) {return <div className="metrics"><Metric label="累计收益率" value={pct(m.total_return)} sub="含期末持仓收盘估值" tone={m.total_return >= 0?'positive':'negative'}/><Metric label="年化收益率" value={pct(m.annual_return)}/><Metric label="最大回撤" value={pct(m.max_drawdown)} sub="收盘权益口径，非盘中" tone="negative"/><Metric label="夏普比率" value={m.sharpe.toFixed(2)}/><Metric label="年化波动" value={pct(m.volatility)}/><Metric label="成交笔数" value={String(m.trade_count)} sub="买卖各计一笔，含再平衡"/></div>}
export function Metric({label,value,sub,tone=''}:{label:string;value:string;sub?:string;tone?:string}) {return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong>{sub && <small>{sub}</small>}</div>}
export function Chart({points, benchmark=true}:{points:Point[];benchmark?:boolean}) {
  const [hover,setHover] = useState<number|null>(null);
  const fillId = useId();
  if(points.length < 2) return <Empty>完成回测或模拟成交后展示净值曲线</Empty>;
  const w=900,h=260,pad=32;
  const values=points.flatMap(p=>benchmark && p.benchmark!==undefined?[p.equity,p.benchmark]:[p.equity]);
  const min=Math.min(...values), max=Math.max(...values), span=max-min||1;
  const x=(i:number)=>pad+i*(w-pad*2)/(points.length-1), y=(v:number)=>h-pad-(v-min)/span*(h-pad*2);
  const line=(key:'equity'|'benchmark')=>points.map((p,i)=>`${x(i)},${y(p[key] ?? p.equity)}`).join(' ');
  const activeIndex = hover === null ? null : Math.min(hover, points.length - 1);
  const selected = activeIndex === null ? null : points[activeIndex];
  return <div className="chart"><svg viewBox={`0 0 ${w} ${h}`} role="img" tabIndex={0}
    aria-label={benchmark ? '策略与基准净值曲线，使用左右方向键查看' : '模拟账户净值曲线，使用左右方向键查看'}
    onFocus={() => setHover(value => value ?? 0)} onBlur={() => setHover(null)}
    onPointerLeave={() => setHover(null)}
    onPointerMove={event => {
      const bounds = event.currentTarget.getBoundingClientRect();
      const index = Math.round(((event.clientX - bounds.left) / bounds.width * w - pad) / (w - pad * 2) * (points.length - 1));
      setHover(Math.max(0, Math.min(points.length - 1, index)));
    }}
    onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home') setHover(0);
      else if (event.key === 'End') setHover(points.length - 1);
      else setHover(Math.max(0, Math.min(points.length - 1, (activeIndex ?? 0) + (event.key === 'ArrowRight' ? 1 : -1))));
    }}>
    <defs><linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--chart-fill)" stopOpacity=".15"/><stop offset="1" stopColor="var(--chart-fill)" stopOpacity="0"/></linearGradient></defs>
    {[0,.25,.5,.75,1].map(t=><g key={t}><line x1={pad} x2={w-pad} y1={y(min+span*t)} y2={y(min+span*t)} stroke="var(--line)" strokeDasharray="4 5"/><text x={pad} y={y(min+span*t)-5} fill="var(--chart-label)" fontSize="10">{money(min+span*t)}</text></g>)}
    <polygon points={`${pad},${h-pad} ${line('equity')} ${w-pad},${h-pad}`} fill={`url(#${fillId})`}/>
    {benchmark&&points[0].benchmark!==undefined&&<polyline points={line('benchmark')} fill="none" stroke="var(--chart-benchmark)" strokeWidth="2" strokeDasharray="5 4"/>}
    <polyline points={line('equity')} fill="none" stroke="var(--chart-equity)" strokeWidth="2.5"/>
    {activeIndex!==null&&<g><line x1={x(activeIndex)} x2={x(activeIndex)} y1={pad} y2={h-pad} stroke="var(--chart-label)"/><circle cx={x(activeIndex)} cy={y(points[activeIndex].equity)} r="4" fill="var(--chart-equity)"/></g>}
    <text x={pad} y={h-7} fontSize="11" fill="var(--chart-label)">{points[0].date.slice(0,10)}</text><text x={w-pad} y={h-7} textAnchor="end" fontSize="11" fill="var(--chart-label)">{points[points.length-1].date.slice(0,10)}</text>
  </svg><div className="chart-legend"><span><i/>{benchmark?'策略净值':'账户净值'}</span>{benchmark&&<span><i className="gray"/>买入持有同标的</span>}<span className="mono" aria-live="polite">{selected?`${selected.date.slice(0,10)} · ${money(selected.equity)}`:'移动指针或使用方向键查看净值'}</span></div></div>
}
