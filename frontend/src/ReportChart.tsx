import {useEffect, useMemo, useRef, useState, type PointerEvent} from 'react';
import {Empty, money, pct} from './components';
import type {Report, Trade} from './types';
import {curveInitial} from './reportReturns';

const priceValue = (value: number | undefined) => value !== undefined && Number.isFinite(value) && value > 0;
const priceText = (value: number | undefined) => value === undefined ? '—' : value.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 6});
const day = (value: string) => value.slice(0, 10);

function domain(values: number[], includeZero = false) {
  let min = includeZero ? 0 : Infinity;
  let max = includeZero ? 0 : -Infinity;
  for (const value of values) {
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  const padding = (max - min || Math.abs(max) * .1 || 1) * .08;
  return [min - padding, max + padding];
}

export function ReportChart({report}: {report: Report}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState<number | null>(null);
  const points = report.curve;
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(300, entries[0].contentRect.width)));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const tradesByDate = useMemo(() => {
    const grouped = new Map<string, Trade[]>();
    for (const trade of report.trades) {
      const key = day(trade.date);
      const trades = grouped.get(key) || [];
      trades.push(trade);
      grouped.set(key, trades);
    }
    return grouped;
  }, [report.trades]);
  const initial = curveInitial(points, report.metrics.total_return, report.initial_equity);
  const initialBenchmark = curveInitial(points, report.metrics.benchmark_return, report.initial_equity, 'benchmark');
  if (points.length < 2 || !priceValue(initial)) return <Empty>暂无足够数据展示走势图</Empty>;
  const height = 340, left = 72, right = 76, top = 34, bottom = 42;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (index: number) => left + index / (points.length - 1) * plotWidth;
  const strategyReturn = (index: number) => points[index].equity / initial! - 1;
  const benchmarkReturn = (index: number) => initialBenchmark && points[index].benchmark !== undefined ? points[index].benchmark! / initialBenchmark - 1 : undefined;
  const baselineByDate = new Map(report.comparison?.baseline_curve.map(point => [point.date, point]));
  const baselineInitial = report.comparison ? curveInitial(report.comparison.baseline_curve, report.comparison.baseline_metrics.total_return, report.comparison.conditions.initial) : undefined;
  const baselineReturn = (index: number) => {
    const point = baselineByDate.get(points[index].date);
    return point && baselineInitial ? point.equity / baselineInitial - 1 : undefined;
  };
  const returns = points.flatMap((_, index) => [strategyReturn(index), benchmarkReturn(index), baselineReturn(index)].filter((value): value is number => value !== undefined));
  const [returnMin, returnMax] = domain(returns, true);
  const priceValues = points.flatMap(point => {
    const trades = tradesByDate.get(day(point.date)) || [];
    return [...(priceValue(point.close) ? [point.close!] : []), ...trades.filter(trade => priceValue(trade.price)).map(trade => trade.price)];
  });
  const hasPrice = points.some(point => priceValue(point.close));
  const [priceMin, priceMax] = domain(priceValues);
  const yReturn = (value: number) => top + (returnMax - value) / (returnMax - returnMin) * plotHeight;
  const yPrice = (value: number) => top + (priceMax - value) / (priceMax - priceMin) * plotHeight;
  const path = (valueAt: (index: number) => number | undefined, y: (value: number) => number) => {
    let previous = false;
    return points.map((_, index) => {
      const value = valueAt(index);
      if (value === undefined || !Number.isFinite(value)) { previous = false; return ''; }
      const command = `${previous ? 'L' : 'M'}${x(index)},${y(value)}`;
      previous = true;
      return command;
    }).join(' ');
  };
  const selectedIndex = hover !== null && hover < points.length ? hover : null;
  const selected = selectedIndex === null ? null : points[selectedIndex];
  const selectedTrades = selected ? tradesByDate.get(day(selected.date)) || [] : [];
  const dateTicks = Array.from(new Set([0, ...(width > 600 ? [Math.floor((points.length - 1) / 2)] : []), points.length - 1]));
  const selectDay = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const position = (event.clientX - rect.left) / rect.width * width;
    setHover(Math.max(0, Math.min(points.length - 1, Math.round((position - left) / plotWidth * (points.length - 1)))));
  };
  return <section className="report-chart" aria-label="收益、单价与成交走势图">
    <div className="report-chart-heading"><h3>收益与每日单价</h3><span className="small muted">{report.symbol} · {report.currency}</span></div>
    <div className="report-chart-plot" ref={container}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="双轴走势图：左轴累计收益率，右轴每日收盘单价；上三角买入，下三角卖出" tabIndex={0}
        onPointerLeave={event => { if (event.pointerType !== 'touch') setHover(null); }} onBlur={() => setHover(null)}
        onPointerDown={selectDay} onPointerMove={selectDay}
        onFocus={() => setHover(points.length - 1)}
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
          event.preventDefault();
          if (event.key === 'Escape') setHover(null);
          else if (event.key === 'Home') setHover(0);
          else if (event.key === 'End') setHover(points.length - 1);
          else setHover(index => Math.max(0, Math.min(points.length - 1, (index ?? points.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1))));
        }}>
        <text x={left} y={16} fill="var(--chart-label)" fontSize="11">累计收益率</text>
        <text x={width - right} y={16} textAnchor="end" fill="var(--chart-price)" fontSize="11">单价 · {report.currency}</text>
        {[0, .25, .5, .75, 1].map(tick => {
          const y = top + tick * plotHeight;
          return <g key={tick}>
            <line x1={left} x2={width - right} y1={y} y2={y} stroke="var(--line)" strokeDasharray="4 5"/>
            <text x={left - 10} y={y + 4} textAnchor="end" fill="var(--chart-label)" fontSize="11">{pct(returnMax - tick * (returnMax - returnMin))}</text>
            {hasPrice && <text x={width - right + 10} y={y + 4} fill="var(--chart-price)" fontSize="11">{money(priceMax - tick * (priceMax - priceMin))}</text>}
          </g>;
        })}
        <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="var(--line)"/>
        <line x1={width - right} x2={width - right} y1={top} y2={height - bottom} stroke="var(--line)"/>
        <path data-series="strategy" d={path(strategyReturn, yReturn)} fill="none" stroke="var(--chart-equity)" strokeWidth="2.5"/>
        {report.comparison && <path data-series="baseline" d={path(baselineReturn, yReturn)} fill="none" stroke="var(--chart-baseline)" strokeWidth="2" strokeDasharray="7 4"/>}
        <path data-series="benchmark" d={path(benchmarkReturn, yReturn)} fill="none" stroke="var(--chart-benchmark)" strokeWidth="1.8" strokeDasharray="5 4"/>
        {hasPrice && <path data-series="price" d={path(index => priceValue(points[index].close) ? points[index].close : undefined, yPrice)} fill="none" stroke="var(--chart-price)" strokeWidth="2"/>}
        {points.flatMap((point, index) => (tradesByDate.get(day(point.date)) || []).map((trade, tradeIndex) => {
          const buying = trade.side === 'buy';
          const cx = x(index), cy = hasPrice ? yPrice(trade.price) : yReturn(strategyReturn(index));
          if (!Number.isFinite(cy)) return null;
          return <g key={`${index}-${tradeIndex}`} data-trade-side={trade.side} onPointerEnter={() => setHover(index)}>
            <path d={buying ? `M${cx},${cy - 7} L${cx - 6},${cy + 5} L${cx + 6},${cy + 5} Z` : `M${cx},${cy + 7} L${cx - 6},${cy - 5} L${cx + 6},${cy - 5} Z`}
              fill={buying ? 'var(--chart-buy)' : 'var(--chart-sell)'} stroke="var(--chart-surface)" strokeWidth="1.5"/>
            <title>{`${day(trade.date)} ${buying ? '买入' : '卖出'} ${trade.quantity} @ ${priceText(trade.price)} ${report.currency}；信号日 ${trade.signal_date}；费用 ${money(trade.fee)}`}</title>
          </g>;
        }))}
        {selectedIndex !== null && <g pointerEvents="none">
          <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={top} y2={height - bottom} stroke="var(--chart-label)" strokeDasharray="3 3"/>
          <circle cx={x(selectedIndex)} cy={yReturn(strategyReturn(selectedIndex))} r="4" fill="var(--chart-equity)"/>
          {priceValue(selected?.close) && <circle cx={x(selectedIndex)} cy={yPrice(selected!.close!)} r="4" fill="var(--chart-price)"/>}
        </g>}
        {dateTicks.map(index => <text key={index} x={x(index)} y={height - 15} textAnchor={index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'} fontSize="11" fill="var(--chart-label)">{day(points[index].date)}</text>)}
      </svg>
      {selected && selectedIndex !== null && <div className="report-chart-tooltip" role="status" aria-live="polite" style={x(selectedIndex) > width / 2 ? {left: 8} : {right: 8}}>
        <strong>{day(selected.date)} · {report.symbol}</strong>
        <dl>
          <dt>{report.comparison ? '调整后累计收益' : '策略累计收益'}</dt><dd>{pct(strategyReturn(selectedIndex))}</dd>
          {report.comparison && <><dt>调整前累计收益</dt><dd>{pct(baselineReturn(selectedIndex))}</dd></>}
          <dt>策略净值 · {report.currency}</dt><dd>{money(selected.equity)}</dd>
          <dt>基准累计收益</dt><dd>{pct(benchmarkReturn(selectedIndex))}</dd>
          {selected.benchmark !== undefined && <><dt>基准净值 · {report.currency}</dt><dd>{money(selected.benchmark)}</dd></>}
          <dt>收盘单价 · {report.currency}</dt><dd>{priceText(selected.close)}</dd>
          <dt>开盘 / 最高 / 最低</dt><dd>{priceText(selected.open)} / {priceText(selected.high)} / {priceText(selected.low)}</dd>
          <dt>成交量 · 上游口径</dt><dd>{selected.volume?.toLocaleString('zh-CN') ?? '—'}</dd>
          <dt>回撤</dt><dd>{pct(selected.drawdown)}</dd>
        </dl>
        {selectedTrades.length ? selectedTrades.map((trade, index) => <div className="report-chart-trade" key={index}>
          <b className={trade.side === 'buy' ? 'positive' : 'negative'}>{trade.side === 'buy' ? '▲ 买入' : '▼ 卖出'}</b>
          <span>{trade.quantity.toLocaleString('zh-CN')} × {priceText(trade.price)} {report.currency}</span>
          <small>信号日 {day(trade.signal_date)} · 费用 {money(trade.fee)} {report.currency}</small>
        </div>) : <small>当日无成交</small>}
      </div>}
    </div>
    <div className="chart-legend report-chart-legend"><span><i/>{report.comparison ? '调整后策略' : '策略累计收益'}</span>{report.comparison && <span><i className="baseline"/>调整前策略 · 重放</span>}<span><i className="gray"/>买入持有同标的</span>{hasPrice && <span><i className="price"/>每日收盘单价</span>}<span className="trade-buy">▲ 买入</span><span className="trade-sell">▼ 卖出</span></div>
    <p className="report-chart-note">收益以初始资金为分母，保留首日盈亏；左右轴独立缩放，单价与成交价沿用回测复权口径。三角标记对应成交日及撮合价，非信号日。悬停、触摸或聚焦后使用左右方向键查看每日数据。</p>
    <p className="report-chart-note">{report.metrics_version === 2 ? '指标口径v2：风险统计含首日损益；满仓买入持有基准从首个允许执行且非零量的开盘起算，忽略费用与手数。' : '历史报告保留原指标与基准口径，未重算首日风险统计；不能与v2直接视为同口径比较。'}</p>
    {!hasPrice && <p className="report-chart-note">{report.chart_warning || '该报告没有可核验的每日价格；仅展示收益曲线，交易标记暂附于收益曲线。'}</p>}
  </section>;
}
