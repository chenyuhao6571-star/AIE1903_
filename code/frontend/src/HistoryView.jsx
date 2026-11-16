import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { request } from './api';

const number = value => value == null ? '—' : value.toLocaleString();
const cost = value => value == null ? '—' : `≈ $${value.toFixed(4)}`;
const time = value => value ? new Date(value).toLocaleString() : '—';
const title = row => row.operation === 'analysis' ? 'Dossier analysis' : 'Candidate score';
const statusLabel = row => row.recovery ? 'recovered' : row.status;
function formatOutput(content) {
  if (typeof content !== 'string') return JSON.stringify(content, null, 2);
  try { return JSON.stringify(JSON.parse(content), null, 2); }
  catch { return content; }
}

export default function HistoryView() {
  const [rows, setRows] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [tab, setTab] = useState('input');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    let timer;
    setError(''); setDetail(null);
    async function poll() {
      try {
        const value = await request(selected ? `/api/requests/${selected.candidate_id}/${selected.id}` : '/api/requests');
        if (!cancelled) { selected ? setDetail(value) : setRows(value); setError(''); }
      } catch (e) { if (!cancelled) setError(e.message); }
      if (!cancelled) timer = setTimeout(poll, 3000);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [selected]);

  return <div className="workspace history-page">
    {error && <p className="error-text" role="alert">{error}</p>}
    {selected ? <>
      <button className="text-link back-link" onClick={() => setSelected(null)}><ArrowLeft size={15}/>Request history</button>
      <div className="page-heading"><h1>{title(selected)}</h1><a href={`#candidate/${selected.candidate_id}/profile`}>{selected.candidate_name}</a></div>
      {!detail && !error ? <p role="status">Loading request…</p> : detail && <>
        <dl className="request-metrics"><div><dt>Status</dt><dd>{statusLabel(detail)}</dd></div><div><dt>Input tokens</dt><dd>{number(detail.input_tokens)}</dd></div><div><dt>Output tokens</dt><dd>{number(detail.output_tokens)}</dd></div><div><dt>Cost</dt><dd>{cost(detail.cost_usd)}</dd></div><div><dt>Started</dt><dd>{time(detail.started_at)}</dd></div>{detail.template_title && <div><dt>Template</dt><dd>{detail.template_title}</dd></div>}</dl>
        {detail.recovery ? <div className="edit-context">Profile recovered from the saved response on {time(detail.recovery.recovered_at)}. No additional model request or charge.<details><summary>Original validation error</summary><p>{detail.error}</p></details></div> : detail.error && <p className="error-text" role="alert">{detail.error}</p>}
        <nav className="main-tabs" aria-label="Request content"><button aria-current={tab === 'input' ? 'page' : undefined} onClick={() => setTab('input')}>Input</button><button aria-current={tab === 'output' ? 'page' : undefined} onClick={() => setTab('output')}>Output</button></nav>
        <div className="request-content">{tab === 'input' ? detail.input ? <>
          <div className="request-model">{detail.input.model}</div>
          {detail.input.messages.map((message, index) => <section key={index}><h2>{message.role === 'system' ? 'System' : message.role === 'user' ? 'User' : message.role}</h2><pre>{message.content}</pre></section>)}
        </> : <p className="empty-note">Input was not recorded for this request.</p>
        : detail.output ? <pre>{formatOutput(detail.output.content ?? detail.output)}</pre> : <p className="empty-note">{detail.status === 'running' ? 'Waiting for output…' : 'No output was recorded.'}</p>}</div>
      </>}
    </> : <>
      <div className="page-heading"><h1>Request history</h1></div>
      {!rows && !error ? <p role="status">Loading requests…</p> : rows && <div className="table-scroll" role="region" aria-label="All requests" tabIndex={0}><table className="data-table history-table"><thead><tr><th>Request</th><th>Candidate</th><th>Started</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th><th>Status</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><button className="table-link" onClick={() => { setSelected(row); setTab('input'); window.scrollTo({ top: 0 }); }}>{title(row)}</button></td><td><a href={`#candidate/${row.candidate_id}/profile`}>{row.candidate_name}</a></td><td className="date-cell">{time(row.started_at)}</td><td className="numeric">{number(row.input_tokens)}</td><td className="numeric">{number(row.output_tokens)}</td><td className="numeric">{cost(row.cost_usd)}</td><td>{statusLabel(row)}</td></tr>)}{!rows.length && <tr><td colSpan={7}>No requests yet.</td></tr>}</tbody></table></div>}
    </>}
  </div>;
}
