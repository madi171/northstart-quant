import {useEffect, useRef, useState} from 'react';
import {BookOpen, Check, Copy, Download, Search} from 'lucide-react';
import {helpArticles, helpDemos, helpManualMarkdown, searchHelp, type HelpArticle, type HelpDemo, type HelpDestination} from './helpContent';
import './help.css';

function downloadText(filename: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], {type: 'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Demo({demo}: {demo: HelpDemo}) {
  const [message, setMessage] = useState('');
  useEffect(() => {if (!message) return; const timer = setTimeout(() => setMessage(''), 4000); return () => clearTimeout(timer);}, [message]);
  async function copy() {
    try {await navigator.clipboard.writeText(demo.content); setMessage('已复制');}
    catch {setMessage('无法访问剪贴板，请选择下方文本复制，或下载示例。');}
  }
  return <section className="help-demo" aria-label={demo.title}>
    <div className="help-demo-heading"><h3>{demo.title}</h3><div className="row"><button type="button" onClick={copy}>{message==='已复制'?<Check size={14}/>:<Copy size={14}/>}复制</button><button type="button" onClick={() => downloadText(demo.filename, demo.content)}><Download size={14}/>下载</button></div></div>
    <p>{demo.description}</p><pre tabIndex={0} aria-label={`${demo.title}内容`}><code>{demo.content}</code></pre><p className="help-demo-note">{demo.note}</p><span role="status">{message}</span>
  </section>;
}

function Article({article, navigate}: {article: HelpArticle; navigate: (page: HelpDestination) => void}) {
  return <article className="help-article" aria-labelledby="help-article-title">
    <header><span className="help-category">{article.category}</span><h2 id="help-article-title">{article.title}</h2><p className="help-lead">{article.summary}</p>{article.destination&&<button type="button" onClick={() => navigate(article.destination!)}>前往{article.destinationLabel}</button>}</header>
    {article.sections.map((section, index) => <section className="help-section" key={section.title} id={`help-section-${index}`}><h3>{section.title}</h3>{section.paragraphs?.map(text => <p key={text}>{text}</p>)}{section.steps&&<ol>{section.steps.map(text => <li key={text}>{text}</li>)}</ol>}{section.bullets&&<ul>{section.bullets.map(text => <li key={text}>{text}</li>)}</ul>}{section.fields&&<div className="help-table-wrap"><table><thead><tr><th>字段 / 概念</th><th>用法与注释</th></tr></thead><tbody>{section.fields.map(([name, description]) => <tr key={name}><th scope="row">{name}</th><td>{description}</td></tr>)}</tbody></table></div>}{section.warning&&<aside className="help-warning"><strong>使用边界</strong><p>{section.warning}</p></aside>}{section.demoIds?.map(id => <Demo key={id} demo={helpDemos.find(demo => demo.id===id)!}/>)}</section>)}
    <footer className="help-related"><h3>继续阅读</h3>{article.related.map(id => {const related = helpArticles.find(item => item.id===id)!; return <a key={id} href={`#help/${id}`}>{related.title}</a>;})}</footer>
  </article>;
}

const articleFromHash = () => helpArticles.find(article => location.hash === `#help/${article.id}`)?.id || helpArticles[0].id;

export function HelpWiki({navigate}: {navigate: (page: HelpDestination) => void}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(articleFromHash);
  const [invalidLink, setInvalidLink] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const results = searchHelp(query);
  const article = helpArticles.find(item => item.id===selected)!;
  useEffect(() => {
    const change = () => {setInvalidLink(location.hash.startsWith('#help/')&&!helpArticles.some(item => location.hash===`#help/${item.id}`)); setSelected(articleFromHash());};
    change(); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change);
  }, []);
  function open(id: string) {setQuery(''); setSelected(id); location.hash=`help/${id}`; content.current?.focus();}
  return <div className="help-wiki">
    <div className="help-intro"><BookOpen size={27}/><div><h2>操作手册与示例</h2><p>从第一笔回测到研究复核，按实际功能查阅。示例只供学习，不自动执行。</p></div><button type="button" onClick={() => downloadText('northstar-help-manual.md', helpManualMarkdown())}><Download size={15}/>导出完整手册</button></div>
    <label className="help-search"><Search size={18}/><span className="sr-only">搜索帮助 Wiki</span><input type="search" placeholder="搜索功能、字段或问题，如：持仓市值、年化、模型配置" value={query} onChange={event => setQuery(event.target.value)}/>{query&&<button type="button" onClick={() => setQuery('')}>清空</button>}</label>
    <div className="help-layout"><nav className="help-toc" aria-label="手册章节"><p>{helpArticles.length} 个章节 · 本地可查</p>{[...new Set(helpArticles.map(item => item.category))].map(category => <div key={category}><h3>{category}</h3>{helpArticles.filter(item => item.category===category).map(item => <a key={item.id} href={`#help/${item.id}`} aria-current={!query&&selected===item.id?'page':undefined} onClick={() => {setQuery('');setSelected(item.id);}}>{item.title}</a>)}</div>)}</nav>
      <div className="help-content" ref={content} tabIndex={-1}>{query.trim()?<section className="help-results"><h2>搜索结果</h2><p role="status">找到 {results.length} 个相关章节</p>{results.length?results.map(item => <button key={item.id} type="button" onClick={() => open(item.id)}><span>{item.category}</span><strong>{item.title}</strong><p>{item.summary}</p></button>):<div className="help-no-results"><h3>没有匹配内容</h3><p>试试更短的关键词，例如“特征”“订单”“缓存”，或清空搜索后浏览目录。</p><button type="button" onClick={() => setQuery('')}>浏览全部章节</button></div>}</section>:<>{invalidLink&&<p className="help-warning" role="status">该章节不存在，已显示快速开始。请从目录重新选择。</p>}<Article key={article.id} article={article} navigate={navigate}/></>}</div>
    </div>
  </div>;
}
