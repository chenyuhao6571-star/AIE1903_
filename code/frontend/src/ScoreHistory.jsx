import { useEffect, useState } from 'react';
import { ArrowLeft, ArrowDownToLine } from 'lucide-react';
import { request } from './api';
import { RubricDetails } from './CriteriaView';

const time = value => value ? new Date(value).toLocaleString() : 'Date unavailable';

export default function ScoreHistory({ job }) {
  const [rows, setRows] = useState(null);
  const [selected, setSelected] = useState(null);
  const [score, setScore] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(''); setScore(null);
    request(`/api/profiles/${job.id}/scores${selected ? `/${selected}` : ''}`)
      .then(data => { if (!cancelled) selected ? setScore(data) : setRows(data); })
      .catch(e => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [job.id, job.updated_at, selected, retry]);

  return <div className="score-history">
    {error && <p className="error-text" role="alert">{error} <button className="text-link" onClick={() => setRetry(v => v + 1)}>Retry</button></p>}
    {selected ? <>
      <button className="text-link back-link" onClick={() => setSelected(null)}><ArrowLeft size={15}/>All saved scores</button>
      {!score && !error && <p role="status">Loading assessment…</p>}
      {score && <>
        <div className="section-heading"><h2>Saved assessment</h2><span className="muted">{time(score.created_at)} · Dossier v{score.revision}</span></div>
        <div className="score-total"><strong>{score.total}<span> / {score.max_score}</span></strong><p>{score.rubric.title}</p></div>
        <details className="selected-criteria"><summary>Original criteria</summary><RubricDetails rubric={score.rubric}/></details>
        <div className="score-list">{score.sections.map(row => {
          const rule = score.rubric.sections.find(section => section.id === row.section_id);
          return <article className="score-row" key={row.section_id}>
            <div className="record-heading"><h3>{row.title || rule?.title || row.section_id}</h3><strong>{row.score} / {row.max_score ?? rule?.max_score}</strong></div>
            <p>{row.rationale}</p>
            {row.status === 'insufficient_evidence' && <p className="attention">Incomplete evidence</p>}
            {!!row.gaps?.length && <ul className="gaps">{row.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul>}
            {row.evidence?.map((e, i) => {
              const source = score.source_snapshot?.find(s => s.number === e.source_id);
              return <blockquote key={i}><p>{e.quote}</p>{source ? <details><summary>{source.filename} · {source.unit === 'github' ? 'GitHub' : `${source.unit === 'block' ? 'block' : 'p.'} ${source.page_number}`}</summary><pre className="saved-source">{source.text}</pre></details> : <span className="muted">Source {e.source_id} · Saved quote</span>}</blockquote>;
            })}
            <details><summary>Scoring rule</summary><p>{row.rules || rule?.rules}</p></details>
          </article>;
        })}</div>
        <div className="section-footer"><a className="text-link" href={`/api/profiles/${job.id}/scores/${selected}?download=true`}><ArrowDownToLine size={15}/>Export saved assessment</a></div>
      </>}
    </> : <>
      {!rows && !error && <p role="status">Loading saved scores…</p>}
      {rows && <div className="table-scroll" role="region" aria-label="Saved scores" tabIndex={0}><table className="data-table"><thead><tr><th>Criteria</th><th>Date</th><th>Dossier</th><th>Score</th><th>Status</th></tr></thead><tbody>
        {rows.map(row => <tr key={row.id}><td><button className="table-link" onClick={() => setSelected(row.id)}>{row.template_title}</button></td><td className="date-cell">{time(row.created_at)}</td><td>v{row.revision}</td><td className="numeric">{row.total} / {row.max_score}</td><td>{row.is_current ? 'Current' : 'Previous'}</td></tr>)}
        {!rows.length && <tr><td colSpan={5}>No saved scores yet.</td></tr>}
      </tbody></table></div>}
    </>}
  </div>;
}
