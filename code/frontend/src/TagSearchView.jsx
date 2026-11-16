import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';
import { request } from './api';

const normalize = text => text.normalize('NFKC').toLocaleLowerCase().trim();

export default function TagSearchView({ onOpen }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState('all');
  useEffect(() => {
    let cancelled = false, timer;
    async function refresh() {
      try { const result = await request('/api/tag-search'); if (!cancelled) { setData(result); setError(''); } }
      catch (e) { if (!cancelled) setError(e.message); }
      if (!cancelled) timer = setTimeout(refresh, 5000);
    }
    refresh();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);
  const matchingTags = (data?.tags || []).filter(tag => (!category || tag.category === category) && normalize(tag.label).includes(normalize(query)));
  const rows = (data?.rows || []).filter(row => {
    const ids = new Set(row.tags.map(tag => tag.id));
    return !selected.length || (mode === 'all' ? selected.every(tag => ids.has(tag.id)) : selected.some(tag => ids.has(tag.id)));
  });
  function toggle(tag) { setSelected(tags => tags.some(item => item.id === tag.id) ? tags.filter(item => item.id !== tag.id) : [...tags, tag]); }
  return <div className="workspace tag-page">
    <header className="page-heading"><h1>Tag search</h1></header>
    {error && <p role="alert" className="error-text">{error}</p>}
    {!data ? <p role="status">Loading tags…</p> : <>
      <div className="tag-search-controls">
        <label className="search"><Search size={17}/><input aria-label="Find a tag" placeholder="Find a tag…" value={query} onChange={e => setQuery(e.target.value)}/></label>
        <select aria-label="Tag category" value={category} onChange={e => setCategory(e.target.value)}><option value="">All categories</option>{[...new Set(data.tags.map(tag => tag.category))].map(value => <option key={value}>{value}</option>)}</select>
      </div>
      <div className="tag-options" aria-label="Available tags">{matchingTags.map(tag => <button className="tag-option" key={tag.id} aria-pressed={selected.some(item => item.id === tag.id)} title={tag.category} onClick={() => toggle(tag)}>{tag.label}<span>{tag.count}</span></button>)}
        {!matchingTags.length && <p className="empty-note">{data.tags.length ? 'No matching tags.' : 'Analyze a dossier to see its profile tags.'}</p>}
      </div>
      {!!selected.length && <div className="selected-tag-row"><div className="selected-tags">{selected.map(tag => <button className="tag-option" aria-label={`Remove tag ${tag.label}`} key={tag.id} onClick={() => toggle(tag)}>{tag.label}<X size={13}/></button>)}<button className="text-link" onClick={() => setSelected([])}>Clear filters</button></div>
        <select aria-label="Tag match mode" value={mode} onChange={e => setMode(e.target.value)}><option value="all">Match all tags</option><option value="any">Match any tag</option></select>
      </div>}
      <div className="section-heading tag-results-heading"><h2>Candidates <span className="muted">{rows.length}</span></h2>{data.excluded > 0 && <span className="muted">{data.excluded} awaiting analysis</span>}</div>
      {rows.length ? <div className="table-scroll"><table className="data-table tag-results"><thead><tr><th>Candidate</th><th>Institution / role</th><th>Profile tags</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}>
        <td><button className="table-link" onClick={() => onOpen(row.id)}>{row.name}</button>{row.manually_edited && <span className="manual-label">Manually edited</span>}</td>
        <td>{row.institution || '—'}{row.position && <div className="muted">{row.position}</div>}</td>
        <td><div className="result-tags">{row.tags.filter(tag => !selected.length || selected.some(item => item.id === tag.id)).slice(0, 6).map(tag => <button key={tag.id} className="tag-option" title={`${tag.category}: ${tag.evidence?.map(item => `${item.section} #${item.record + 1} · sources ${item.source_pages.join(', ')}`).join('; ')}`} onClick={() => toggle(tag)}>{tag.label}</button>)}</div>{row.tags.length > 6 && !selected.length && <span className="muted">+{row.tags.length - 6} more</span>}</td>
      </tr>)}</tbody></table></div> : <p className="empty-note">{selected.length ? 'No candidates match these tags.' : 'No analyzed profiles yet.'}</p>}
    </>}
  </div>;
}
