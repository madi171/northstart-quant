import {parseUnifiedDiff} from './researchDiff';

export interface ResearchExplanation {
  name?: string;
  strategy_logic?: string;
  change_summary?: string;
  naming_reason?: string;
  naming_source?: string;
  notes?: string;
}
export interface ResearchChange {
  changed_lines: number;
  param_changes: Record<string, {before: number | null; after: number | null}>;
  diff: string;
}

export function StrategyExplanation({strategy}: {strategy: ResearchExplanation}) {
  return <div className="research-explanation">
    <dl>
      <div><dt>策略逻辑</dt><dd>{strategy.strategy_logic || '此记录未保存完整策略描述，请结合候选代码核验。'}</dd></div>
      <div><dt>改了什么</dt><dd>{strategy.change_summary || '未保存文字改动摘要；如有差异记录，请查看下方实际代码与参数变化。'}</dd></div>
      <div><dt>为什么这样改</dt><dd>{strategy.notes || '此记录未保存修改理由。'}</dd></div>
      {strategy.naming_reason && <div><dt>命名依据</dt><dd>{strategy.naming_reason}</dd></div>}
    </dl>
    <p className="small muted">以上为提案说明，并非已验证结论；实际修改以差异为准，效果以评估结果为准。</p>
  </div>;
}

export function StrategyDiff({change, parentLabel = '本轮父版本'}: {change?: ResearchChange; parentLabel?: string}) {
  if (!change) return <p className="small muted">此记录未保存差异，未自动推断或重新调用模型补写。</p>;
  const params = Object.entries(change.param_changes || {});
  const lines = change.diff ? parseUnifiedDiff(change.diff) : [];
  return <section className="research-diff" aria-label="代码与参数差异">
    <div className="research-diff-heading"><strong>实际改动</strong><span>相对{parentLabel} · {change.changed_lines} 行代码 · {params.length} 个参数</span></div>
    {lines.length ? <><p className="small muted">代码差异 · − 删除 / + 新增 · 左列原行号，右列候选行号</p><pre className="research-diff-code" tabIndex={0} aria-label="代码差异，可横向滚动"><code>{lines.map((line, index) => <span key={index} className={'research-diff-line diff-' + line.kind}><span className="diff-number" aria-hidden="true">{line.before ?? ''}</span><span className="diff-number" aria-hidden="true">{line.after ?? ''}</span><span>{line.text}</span>{'\n'}</span>)}</code></pre></> : <p className="small muted">代码无变化</p>}
    {params.length ? <div className="table-wrap"><table className="research-param-diff"><caption>参数差异 · 修改前与修改后</caption><thead><tr><th>参数</th><th>修改前（−）</th><th>修改后（+）</th><th>变化</th></tr></thead><tbody>{params.map(([key, value]) => <tr key={key}><th scope="row"><code>{key}</code></th><td className="diff-param-before">{value.before === null ? '未设置' : String(value.before)}</td><td className="diff-param-after">{value.after === null ? '已移除' : String(value.after)}</td><td>{value.before === null ? '新增' : value.after === null ? '移除' : '调整'}</td></tr>)}</tbody></table></div> : <p className="small muted">参数无变化</p>}
  </section>;
}

export function NamingSource({source}: {source?: string}) {
  return <span className="research-name-source">{source === 'model' ? '模型命名' : source === 'demo' ? '演示命名' : source === 'fallback' ? '模型未提供名称 · 系统回退' : '沿用记录名称'}</span>;
}
