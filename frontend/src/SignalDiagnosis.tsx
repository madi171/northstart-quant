import type {Report} from './types';

export function SignalDiagnosis({report}: {report: Report}) {
  const diagnostic = report.signal_diagnostics;
  if (report.metrics.trade_count !== 0) return null;
  return <section className="signal-diagnosis" aria-label="无成交原因诊断">
    <div className="section-row"><h3>无成交原因诊断</h3><span className="tag">{diagnostic?.replayed ? '原快照只读重放' : 'SIGNAL DIAGNOSTICS'}</span></div>
    <div className="alert" role="status">{diagnostic?.summary || report.diagnostic_warning || '当前报告未记录信号诊断，不能仅凭零收益判断是未触发入场还是无法成交。'}</div>
    {diagnostic && <>
      <dl className="signal-diagnosis-facts">
        <div><dt>历史日线</dt><dd>{diagnostic.signal_days} 根</dd></div>
        <div><dt>预热日线</dt><dd>{diagnostic.warmup_days === null ? '自定义代码，未推断' : diagnostic.warmup_days + ' 根'}</dd></div>
        <div><dt>策略正仓位信号</dt><dd>{diagnostic.raw_positive_days} 天</dd></div>
        <div><dt>风险裁剪后正仓位</dt><dd>{diagnostic.positive_target_days} 天</dd></div>
      </dl>
      {!!diagnostic.zero_return_branches.length && <div className="table-scroll"><table><thead><tr><th>实际返回零仓位的分支</th><th>代码行</th><th>天数</th></tr></thead><tbody>{diagnostic.zero_return_branches.map(branch => <tr key={branch.line}><td>{branch.label}</td><td><code>{branch.line}</code></td><td>{branch.days}</td></tr>)}</tbody></table></div>}
      {diagnostic.raw_positive_days > 0 && <dl className="signal-diagnosis-facts">
        <div><dt>有下一日可撮合的信号</dt><dd>{diagnostic.eligible_signal_days} 天</dd></div>
        <div><dt>次日成交量为零</dt><dd>{diagnostic.zero_volume_days} 天</dd></div>
        <div><dt>目标仓位不足一手</dt><dd>{diagnostic.below_lot_days} 天</dd></div>
        <div><dt>费用后现金不足</dt><dd>{diagnostic.insufficient_cash_days} 天</dd></div>
      </dl>}
      <p className="small muted">{diagnostic.note}</p>
    </>}
    <p className="small muted">0 成交表示该区间未建立持仓，并非已验证盈利或低风险。可用下方“基于此实验调整策略”检查参数；不要只为产生交易放宽过滤，调整后应另选样本外区间验证。</p>
  </section>;
}
