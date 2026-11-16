import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import ComparisonEvidence from './ComparisonEvidence';
import { fraction, heatColor, incomplete, sectionColor, sortRows } from './comparisonUtils';
import './comparison.css';

export default function ComparisonView({ templates }) {
  const [templateId, setTemplateId] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [view, setView] = useState('table');
  const [sort, setSort] = useState({ column: 'name', direction: 1 });
  const [selected, setSelected] = useState(null);
  const [hover, setHover] = useState('');
  const cache = useRef(new Map());
  const selectedHash = templates.find(template => template.id === templateId)?.rubric_hash;

  useEffect(() => {
    let cancelled = false, timer;
    setData(null); setError(''); setSelected(null); setHover('');
    async function poll() {
      try {
        const result = await request(`/api/comparison${templateId ? `?template_id=${encodeURIComponent(templateId)}` : ''}`);
        if (templateId) await Promise.all(result.rows.filter(row => row.total != null).map(async row => {
          const key = `${row.id}:${row.rubric_hash}:${row.created_at}`;
          try {
            if (!cache.current.has(key)) {
              const history = await request(`/api/profiles/${row.id}/scores`);
              const saved = history.find(score => score.created_at === row.created_at && score.template_title === row.template_title);
              if (!saved) throw new Error('Assessment unavailable');
              const assessment = await request(`/api/profiles/${row.id}/scores/${saved.id}`);
              // Match the exact saved rubric and run, even if another template is selected on the candidate.
              if (assessment.rubric_hash !== row.rubric_hash || assessment.created_at !== row.created_at) throw new Error('Assessment changed');
              cache.current.set(key, assessment);
            }
            row.assessment = cache.current.get(key);
          } catch { row.evidenceUnavailable = true; }
        }));
        if (!cancelled) { setData(result); setError(''); }
      } catch (e) { if (!cancelled) setError(e.message); }
      if (!cancelled) timer = setTimeout(poll, 3000);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [templateId, selectedHash]);

  const rows = sortRows(data?.rows || [], sort);
  const sections = data?.template?.sections || [];
  const maximum = sections.reduce((total, section) => total + section.max_score, 0);
  const changeSort = column => setSort(current => ({ column, direction: current.column === column ? -current.direction : column === 'name' ? 1 : -1 }));
  const ariaSort = column => sort.column === column ? (sort.direction === 1 ? 'ascending' : 'descending') : 'none';
  const arrow = column => sort.column === column ? (sort.direction === 1 ? ' ↑' : ' ↓') : '';
  const open = (row, sectionId = null) => setSelected({ row, sectionId });
  const scoreLabel = (row, section) => `${row.name} · ${section.title}: ${row.sections[section.id]} / ${section.max_score}${incomplete(row, section.id) ? ' · Incomplete evidence' : ''}`;

  function table(heatmap = false) {
    return <div className="table-scroll" tabIndex={0} role="region" aria-label={heatmap ? 'Score heatmap' : 'Candidate scores'}><table className={`data-table comparison-table ${heatmap ? 'comparison-heatmap' : ''}`}>
      <thead><tr><th aria-sort={ariaSort('name')}><button onClick={() => changeSort('name')}>Candidate{arrow('name')}</button></th>{!templateId && <th>Template</th>}{sections.map(section => <th key={section.id} aria-sort={ariaSort(section.id)}><button onClick={() => changeSort(section.id)}>{section.title}{arrow(section.id)}</button><span className="column-max">/ {section.max_score}</span></th>)}<th aria-sort={ariaSort('total')}>{templateId ? <button onClick={() => changeSort('total')}>Total{arrow('total')}</button> : 'Total'}</th><th>Status</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}><td><a href={`#candidate/${row.id}/profile`}>{row.name}</a></td>{!templateId && <td>{row.template_title || '—'}</td>}{sections.map(section => {
        const value = row.sections[section.id], ratio = fraction(value, section.max_score);
        return <td className="numeric" key={section.id}>{value == null ? <span className="missing-score" aria-label="Not scored">—</span> : <button className={`comparison-cell ${heatmap ? 'heat-cell' : ''}`} style={heatmap ? { backgroundColor: heatColor(value, section.max_score), color: ratio >= .97 ? '#fff' : '#000' } : undefined} aria-label={scoreLabel(row, section)} title={scoreLabel(row, section)} onClick={() => open(row, section.id)}>{heatmap ? `${value} / ${section.max_score}` : value}{incomplete(row, section.id) && <span className="evidence-marker" aria-label="Incomplete evidence">!</span>}</button>}</td>;
      })}<td className="numeric score-cell">{row.total == null ? '—' : templateId ? <button className="table-link" onClick={() => open(row)}>{row.total} / {row.max_score}</button> : `${row.total} / ${row.max_score}`}</td><td><span className={row.status === 'Scored' ? 'scored-status' : 'muted'}>{row.status}</span>{row.evidenceUnavailable && <span className="manual-label">Evidence unavailable</span>}</td></tr>)}{!rows.length && <tr><td colSpan={sections.length + (templateId ? 3 : 4)}>No candidates yet.</td></tr>}</tbody>
    </table></div>;
  }

  return <div className="workspace comparison-page">
    <div className="page-heading"><h1>Comparison</h1><label className="field template-filter">Criteria template<select value={templateId} onChange={e => { setTemplateId(e.target.value); setSort({ column: 'name', direction: 1 }); }}><option value="">All templates</option>{templates.map(template => <option key={template.id} value={template.id}>{template.title}</option>)}</select></label></div>
    <nav className="main-tabs comparison-views" aria-label="Comparison views">{[['table', 'Table'], ['breakdown', 'Score breakdown'], ['heatmap', 'Heatmap']].map(([id, label]) => <button key={id} aria-current={view === id ? 'page' : undefined} onClick={() => { setView(id); setHover(''); }}>{label}</button>)}</nav>
    {error && <p className="error-text" role="alert">{error}</p>}
    {!data && !error ? <p role="status">Loading comparison…</p> : data && <>
      {view !== 'table' && !templateId ? <p className="empty-note">Choose a criteria template to compare scores on the same scale.</p> : view === 'breakdown' ? <>
        <div className="breakdown-toolbar"><div className="breakdown-legend">{sections.map(section => <span key={section.id}><i style={{ background: sectionColor(section) }}/>{section.title} <span className="muted">/{section.max_score}</span></span>)}</div><label className="field">Sort by<select value={`${sort.column}:${sort.direction}`} onChange={e => { const [column, direction] = e.target.value.split(':'); setSort({ column, direction: Number(direction) }); }}><option value="name:1">Name A–Z</option><option value="name:-1">Name Z–A</option><option value="total:-1">Highest total</option><option value="total:1">Lowest total</option>{sections.flatMap(section => [<option key={`${section.id}-desc`} value={`${section.id}:-1`}>{section.title}: highest</option>, <option key={`${section.id}-asc`} value={`${section.id}:1`}>{section.title}: lowest</option>])}</select></label></div>
        <div className="breakdown-scale"><span/><div>{[0, .25, .5, .75, 1].map(value => <span key={value}>{Math.round(maximum * value * 100) / 100}</span>)}</div><span/></div>
        <div className="breakdown-chart" aria-label="Scores by criterion">{rows.map(row => <div className="breakdown-row" key={row.id}>
          <a href={`#candidate/${row.id}/profile`}>{row.name}</a>
          {row.total == null ? <div className="unscored-bar">{row.status}</div> : <div className="breakdown-track">{sections.map(section => {
            const value = row.sections[section.id];
            if (!(value > 0)) return null;
            return <button key={section.id} className={`breakdown-segment ${incomplete(row, section.id) ? 'incomplete-segment' : ''}`} style={{ width: `${fraction(value, maximum) * 100}%`, backgroundColor: sectionColor(section) }} aria-label={scoreLabel(row, section)} title={scoreLabel(row, section)} onMouseEnter={() => setHover(scoreLabel(row, section))} onMouseLeave={() => setHover('')} onFocus={() => setHover(scoreLabel(row, section))} onBlur={() => setHover('')} onClick={() => open(row, section.id)}>{value / maximum >= .05 ? value : ''}</button>;
          })}{row.total === 0 && <button className="zero-score-button" onClick={() => open(row)}>0 · View assessment</button>}</div>}
          {row.total == null ? <span className="muted">—</span> : <button className="table-link numeric" aria-label={`View assessment for ${row.name}`} onClick={() => open(row)}>{row.total} / {maximum}{row.assessment?.sections.some(section => section.status === 'insufficient_evidence') && <span className="evidence-marker" aria-label="Incomplete evidence">!</span>}</button>}
        </div>)}</div>
        {!rows.length && <p className="empty-note">No candidates yet.</p>}
        <p className="chart-hover" role="status">{hover || 'Select a segment or total to view evidence.'}</p>
      </> : table(view === 'heatmap')}
      {templateId && <div className="comparison-key">{view === 'heatmap' && <span><i className="heat-gradient"/>0–100% of each criterion’s maximum</span>}<span><b className="evidence-marker">!</b> Incomplete evidence</span><span>— Not scored</span></div>}
    </>}
    {selected && <ComparisonEvidence {...selected} onClose={() => setSelected(null)}/>}
  </div>;
}
