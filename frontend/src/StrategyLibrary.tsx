import {useState} from 'react';
import {BookOpen, Plus, Settings2} from 'lucide-react';
import {api} from './api';
import {createClientKey} from './clientKey';
import {Modal} from './components';
import type {Strategy, StrategyTemplate} from './types';

type Props = {templates: StrategyTemplate[]; busy: boolean; added: (strategy: Strategy) => void};

export function StrategyLibrary({templates, busy, added}: Props) {
  const [selected, setSelected] = useState<StrategyTemplate | null>(null);
  const [adding, setAdding] = useState('');
  const [error, setError] = useState('');
  const [keys] = useState(() => new Map<string, string>());
  async function quickAdd(template: StrategyTemplate) {
    if (adding || busy) return;
    setAdding(template.id); setError('');
    const clientKey = keys.get(template.id) || createClientKey();
    keys.set(template.id, clientKey);
    try {
      const strategy = await api<Strategy>('/strategy-templates/' + template.id + '/instantiate', {
        name: template.name, code: template.code, params: template.params,
        notes: template.description + '\n' + template.limitations, client_key: clientKey,
      });
      added(strategy);
    } catch (failure) {setError((failure as Error).message);} finally {setAdding('');}
  }
  if (!templates.length) return null;
  return <section className="strategy-library">
    <div className="section-row"><div><h3>研究策略模板库 <span className="subtle">{templates.length} 个可编辑实现</span></h3><p className="small muted">单标的日线 · 只做多 · 波动率风险预算 · 人工批准</p></div><span className="tag">RESEARCH TEMPLATES</span></div>
    <div className="alert">SOTA 不能仅凭策略名称判定。这些是有研究依据的规则型模板，尚未对你的标的完成样本外验证；不是最优收益承诺，也不是已训练的 AI 交易模型。</div>
    {error && <div className="alert danger" role="alert">{error}</div>}
    <div className="strategy-template-grid">{templates.map(template => <article className="strategy-template-card" key={template.id}>
      <div className="row between"><span className="tag green">{template.category}</span><small className="muted">v{template.version}</small></div>
      <h3>{template.name}</h3><p>{template.description}</p>
      <div className="template-facts"><span>默认预热 <b>{template.warmup} 根日线</b></span><span>最大目标仓位 <b>{(template.params.weight * 100).toFixed(0)}%</b></span></div>
      <details><summary><BookOpen size={13}/>适用边界与参考</summary><p>{template.limitations}</p>{template.references.map(reference => <a key={reference.url} href={reference.url} target="_blank" rel="noreferrer">{reference.title}</a>)}</details>
      <footer><button type="button" disabled={!!adding || busy} onClick={() => setSelected(template)}><Settings2 size={14}/>配置 / 查看代码</button><button type="button" className="primary" disabled={!!adding || busy} onClick={() => quickAdd(template)}><Plus size={14}/>{adding === template.id ? '添加中…' : '一键添加'}</button></footer>
    </article>)}</div>
    <p className="small muted top-gap">预热从实验起点开始，至少应覆盖预热期并留出足够评估交易日。平台仓位上限可能进一步裁剪目标仓位；收盘信号在下一交易日开盘撮合。</p>
    {selected && <TemplateForm key={selected.id} template={selected} busy={busy} close={() => setSelected(null)} saved={strategy => {setSelected(null); added(strategy);}}/>}
  </section>;
}

function TemplateForm({template, busy, close, saved}: {template: StrategyTemplate; busy: boolean; close: () => void; saved: (strategy: Strategy) => void}) {
  const [params, setParams] = useState({...template.params});
  const [code, setCode] = useState(template.code);
  const [name, setName] = useState(template.name);
  const [notes, setNotes] = useState(template.description + '\n' + template.limitations);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [clientKey, setClientKey] = useState(() => createClientKey());
  const changed = () => {setClientKey(createClientKey());setError('');};
  const locked = saving || busy;
  return <Modal title={'配置研究策略 · ' + template.name} wide close={() => {if (!locked) close();}}>
    <form className="form template-form" onSubmit={async event => {
      event.preventDefault(); if (locked) return; setSaving(true);setError('');
      try {
        const strategy = await api<Strategy>('/strategy-templates/' + template.id + '/instantiate', {name, code, params, notes, client_key:clientKey});
        saved(strategy);
      } catch (failure) {setError((failure as Error).message);} finally {setSaving(false);}
    }}>
      <div className="alert">{template.description}<br/>{template.limitations}</div>
      <label>策略版本名称<input required maxLength={80} value={name} disabled={locked} onChange={event => {setName(event.target.value);changed();}}/></label>
      <fieldset><legend>参数与风险预算</legend><div className="template-parameter-grid">{template.parameters.map(parameter => <label key={parameter.key}>{parameter.label}<input type="number" required min={parameter.min} max={parameter.max} step={parameter.integer ? 1 : 'any'} value={Number.isNaN(params[parameter.key]) ? '' : params[parameter.key]} disabled={locked} onChange={event => {setParams({...params, [parameter.key]:event.target.valueAsNumber});changed();}}/><small><code>{parameter.key}</code> · {parameter.min}–{parameter.max}{parameter.integer ? '，整数' : ''}</small></label>)}</div></fieldset>
      <label>策略代码<textarea className="code-editor" rows={15} value={code} required maxLength={12000} disabled={locked} spellCheck={false} onChange={event => {setCode(event.target.value);changed();}}/></label>
      <label>版本说明<textarea rows={3} maxLength={2000} value={notes} disabled={locked} onChange={event => {setNotes(event.target.value);changed();}}/></label>
      <p className="small muted">所有指标仅使用截至当日的历史收盘数据；可使用 ema / realized_vol / rsi / kama / efficiency_ratio / zscore / channel_signal。自定义代码仍通过原 AST 沙箱校验。</p>
      <p className="small muted">保存后可在回测实验室创建回测，或在 Auto Research 选择此策略。</p>
      {error && <div className="alert danger" role="alert">{error}</div>}
      <footer><button type="button" disabled={locked} onClick={close}>取消</button><button className="primary" disabled={locked}>{saving ? '保存中…' : '保存为待回测策略'}</button></footer>
    </form>
  </Modal>;
}
