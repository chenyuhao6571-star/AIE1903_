import { useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowUpRight, Check, ClipboardList, LoaderCircle, Pencil, Plus, Sparkles, Trash2, X } from 'lucide-react';
import { QUESTION_FACTORS as FACTORS } from './questionFactors';
import { request, jsonRequest } from './api';
import './questions.css';

export default function QuestionsView({ job, onSource, onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [estimate, setEstimate] = useState(null);
  const [estimateError, setEstimateError] = useState('');
  const [estimateAttempt, setEstimateAttempt] = useState(0);
  const [filter, setFilter] = useState('all');
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState({ text: '', factor: 'teaching', clarification: '', criteria: '' });
  const [replace, setReplace] = useState(false);
  const [removed, setRemoved] = useState(null);
  const [overview, setOverview] = useState(false);
  const stateRef = useRef();
  const savingRef = useRef(false);
  const mounted = useRef(true);
  const questions = data?.questions || [];
  const generating = data?.status === 'generating';
  const locked = generating || saving || !data?.can_generate || data?.stale;
  const base = `/api/profiles/${job.id}/questions`;
  const candidateDialog = useRef();
  const overviewRef = useRef();
  const approved = questions.filter(q => q.approved);
  const visible = questions.filter(q => filter === 'all' || q.factor === filter);
  const position = job.rubric?.title || job.score?.rubric?.title;
  function accept(next) {
    if (!mounted.current) return;
    if (stateRef.current && next.version < stateRef.current.version) return;
    stateRef.current = next; setData(next);
  }
  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer;
    async function poll() {
      try { const next = await request(base); if (!stopped) { accept(next); setLoadError(''); } }
      catch (e) { if (!stopped) setLoadError(e.message); }
      if (!stopped) timer = setTimeout(poll, 2000);
    }
    poll();
    return () => { stopped = true; mounted.current = false; clearTimeout(timer); };
  }, [base]);
  useEffect(() => {
    let ignore = false;
    setEstimate(null); setEstimateError('');
    if (data?.can_generate) request(`${base}/estimate`).then(value => { if (!ignore) setEstimate(value); })
      .catch(e => { if (!ignore) setEstimateError(e.message); });
    return () => { ignore = true; };
  }, [base, data?.can_generate, data?.current_context_hash, estimateAttempt]);

  async function commit(next) {
    if (savingRef.current || !stateRef.current) return false;
    savingRef.current = true; setSaving(true); setError('');
    try {
      accept(await request(base, jsonRequest('PUT', { version: stateRef.current.version, questions: next })));
      return true;
    } catch (e) {
      if (mounted.current) setError(e.message);
      try { accept(await request(base)); } catch { /* Keep the actionable mutation error. */ }
      return false;
    } finally { savingRef.current = false; if (mounted.current) setSaving(false); }
  }

  async function generate() {
    if (!estimate || savingRef.current) return;
    savingRef.current = true; setSaving(true); setError(''); setReplace(false);
    try {
      accept(await request(`${base}/generate`, jsonRequest('POST', { version: stateRef.current.version, context_hash: estimate.context_hash, replace: questions.length > 0 })));
      setEditing(null); setRemoved(null); setFilter('all');
    } catch (e) { setError(e.message); setEstimateAttempt(n => n + 1); }
    finally { savingRef.current = false; if (mounted.current) setSaving(false); }
  }
  function startEdit(question) {
    const factor = question?.factor || (filter === 'all' ? 'teaching' : filter);
    setEditing(question?.id || 'new');
    setDraft({ text: question?.text || '', factor,
      clarification: question?.clarification || '',
      criteria: (question?.criteria || []).join('\n') });
  }
  function changeFactor(factor) {
    setDraft(value => ({ ...value, factor }));
  }
  async function save(e) {
    e.preventDefault();
    const criteria = draft.criteria.split('\n').map(line => line.trim()).filter(Boolean);
    if (!draft.text.trim() || !draft.clarification.trim() || !criteria.length) return;
    const fields = { text: draft.text.trim(), factor: draft.factor, clarification: draft.clarification.trim(), criteria, approved: false };
    const rows = stateRef.current.questions;
    const next = editing === 'new' ? [...rows, { ...fields, id: crypto.randomUUID(), evidence: '', sources: [] }]
      : rows.map(q => q.id === editing ? { ...q, ...fields } : q);
    if (await commit(next)) { if (editing === 'new') setFilter('all'); setEditing(null); }
  }
  function editor(isNew) {
    return <form className="question-editor" onSubmit={save}>
      {isNew && <label className="field">Factor<select value={draft.factor} onChange={e => changeFactor(e.target.value)}>{FACTORS.map(f => <option key={f.id} value={f.id}>{f.title}</option>)}</select></label>}
      <label className="field">Needs clarification<textarea rows={2} maxLength={1000} value={draft.clarification} onChange={e => setDraft(value => ({ ...value, clarification: e.target.value }))} required/></label>
      <label className="field">Question<textarea autoFocus rows={3} maxLength={2000} value={draft.text} onChange={e => setDraft(value => ({ ...value, text: e.target.value }))} required/></label>
      <label className="field">Analysis criteria<textarea rows={4} maxLength={3000} value={draft.criteria} onChange={e => setDraft(value => ({ ...value, criteria: e.target.value }))} required/></label>
      <div className="interview-actions"><button className="button primary" disabled={locked || !draft.text.trim() || !draft.clarification.trim() || !draft.criteria.trim()} type="submit">{saving ? 'Saving…' : 'Save question'}</button><button className="button secondary" disabled={saving} type="button" onClick={() => setEditing(null)}>Cancel</button></div>
    </form>;
  }
  function showOverview() {
    setOverview(true);
    requestAnimationFrame(() => overviewRef.current?.scrollIntoView({ block: 'start', behavior: 'instant' }));
  }
  return <div className="questions-view">
    <div className="interview-heading"><h2>Questions</h2><div className="questions-generation"><span>{estimate?.estimated_usd != null ? `≈ $${estimate.estimated_usd.toFixed(4)}` : ''}</span><button className="button primary" disabled={generating || saving || !estimate || !data?.can_generate} onClick={() => questions.length ? setReplace(true) : generate()}>{generating ? <LoaderCircle size={16} className="spin"/> : <Sparkles size={16}/>} {generating ? 'Generating…' : 'Generate questions'}</button></div></div>
    {(error || data?.error || estimateError || loadError) && <p className="error-text" role="alert">{error || data?.error || estimateError || loadError}{estimateError && <button className="text-link" onClick={() => setEstimateAttempt(n => n + 1)}>Refresh</button>}</p>}
    {data?.context_error && <p className="attention">{data.context_error}</p>}
    {data?.stale && <p className="attention" role="status">Profile or position changed. Generate new questions.</p>}
    {!data && !error && !loadError && <p role="status"><LoaderCircle size={16} className="spin"/></p>}
    {replace && <div className="interview-confirm" role="status"><p>Replace questions and approvals?</p><div className="interview-actions"><button className="button primary" disabled={saving || generating} onClick={generate}>Replace</button><button className="button secondary" onClick={() => setReplace(false)}>Cancel</button></div></div>}
    <div className="coverage-heading"><h3>Profile coverage</h3>{position && <span>{position}</span>}</div>
    <div className="coverage-factors">{FACTORS.map(f => { const coverage = data?.factors.find(row => row.id === f.id)?.coverage; return <button className={`coverage-factor ${coverage != null && coverage < 50 ? 'needs-detail' : ''}`} key={f.id} aria-pressed={filter === f.id} onClick={() => setFilter(filter === f.id ? 'all' : f.id)}>
      <span>{f.title}</span><strong>{coverage ?? '—'}{coverage != null && <small>/100</small>}</strong><span className="coverage-track"><span style={{ width: `${coverage ?? 0}%` }}/></span>
    </button>; })}</div>
    <div className="question-toolbar"><div><h3>{filter === 'all' ? 'Interview questions' : FACTORS.find(f => f.id === filter).title} <span>{visible.length}</span></h3><p>{approved.length} / {questions.length} approved{filter !== 'all' && <> · <button className="text-link" onClick={() => setFilter('all')}>Show all</button></>}</p></div><div className="interview-actions"><button className="button secondary" disabled={locked} onClick={() => startEdit()}><Plus size={16}/>Add question</button><button className="button secondary" disabled={locked || !questions.length || approved.length === questions.length || editing !== null} onClick={() => commit(questions.map(q => ({ ...q, approved: true })))}><Check size={16}/>Approve all</button></div></div>
    {removed && <div className="interview-status" role="status">Question removed<button className="text-link" disabled={locked} onClick={async () => { const next = [...stateRef.current.questions]; next.splice(Math.min(removed.index, next.length), 0, removed.question); if (await commit(next)) setRemoved(null); }}>Undo</button></div>}
    {editing === 'new' && editor(true)}
    {!!visible.length && <div className="question-pair-heading" aria-hidden="true"><span>Needs clarification</span><span/><span>Question</span></div>}
    <div className="question-list" aria-busy={generating}>{visible.map(q => {
      const factor = FACTORS.find(f => f.id === q.factor);
      const index = questions.indexOf(q);
      return <article className={`question-pair ${q.approved ? 'is-approved' : 'is-draft'}`} key={q.id} aria-label={`Question ${index + 1}: ${q.approved ? 'Approved' : 'Draft'}`}>
        <div className="question-gap"><div className="question-gap-meta"><span className="question-number">{String(index + 1).padStart(2, '0')}</span><h3>{factor.title}</h3></div><span className="question-mobile-label">Needs clarification</span><p>{q.clarification}</p>
          {(q.evidence || q.sources.length > 0) && <details className="question-evidence"><summary>Source</summary>{q.evidence && <p>{q.evidence}</p>}<div className="sources">{q.sources.map(id => {
            const source = job.sources?.find(s => s.number === id);
            return source && <button className="text-link" disabled={data?.stale} key={id} onClick={() => onSource(id)}>{source.filename} · {source.unit === 'block' ? 'block' : 'p.'} {source.page_number}<ArrowUpRight size={12}/></button>;
          })}</div></details>}
        </div>
        <ArrowRight className="question-connector" size={22} aria-hidden="true"/>
        <div className="question-content"><div className="question-meta"><span className="question-mobile-label">Question</span><span className={q.approved ? 'question-approved' : 'question-draft'}>{q.approved ? <><Check size={13}/>Approved</> : 'Draft'}</span></div>
          {editing === q.id ? editor(false) : <h3>{q.text}</h3>}
          <div className="question-controls"><button className="text-link" disabled={locked || editing === q.id} onClick={() => commit(questions.map(row => row.id === q.id ? { ...row, approved: !row.approved } : row))}><Check size={14}/>{q.approved ? 'Return to draft' : 'Approve'}</button><button className="text-link" disabled={locked} onClick={() => startEdit(q)}><Pencil size={14}/>Edit</button><button className="text-link question-remove" disabled={locked} onClick={async () => { if (await commit(questions.filter(row => row.id !== q.id))) { setRemoved({ question: q, index }); if (editing === q.id) setEditing(null); } }}><Trash2 size={14}/>Remove</button></div>
        </div>
      </article>;
    })}</div>
    {!visible.length && <p className="empty-note">No questions</p>}
    <div className="question-overview-action"><button className="button primary" disabled={!approved.length || generating} onClick={showOverview}><ClipboardList size={17}/>Build question overview</button></div>
    {overview && <section ref={overviewRef} className="question-overview" aria-label="Approved question overview"><div className="section-heading"><h2>Approved questions & analysis <span>{approved.length}</span></h2><button className="text-link" onClick={() => setOverview(false)}>Hide overview</button></div>
      {!!approved.length && <div className="question-overview-heading" aria-hidden="true"><span>Question</span><span>Analysis criteria</span></div>}
      {approved.map((q, index) => <article className="question-overview-row is-approved" key={q.id} aria-label={`Approved question ${index + 1}`}><div><div className="question-meta"><span>{String(index + 1).padStart(2, '0')} · {FACTORS.find(f => f.id === q.factor).title}</span><span className="question-approved"><Check size={13}/>Approved</span></div><h3>{q.text}</h3></div><div><h3 className="question-mobile-label">Analysis criteria</h3><ul>{q.criteria.map((criterion, i) => <li key={i}>{criterion}</li>)}</ul></div></article>)}
      {!approved.length && <p className="empty-note" role="status">No approved questions</p>}
    </section>}
    <div className="interview-bottom"><div className="interview-actions"><button className="button secondary" disabled={!approved.length || data?.stale} onClick={() => candidateDialog.current.showModal()}>Preview candidate view</button><button className="button secondary" onClick={() => onNavigate('video')}>Video Interview Analysis<ArrowRight size={16}/></button></div></div>
    <dialog ref={candidateDialog} className="modal interview-candidate-dialog"><div className="modal-heading"><h2>Interview questions</h2><button className="icon-button" aria-label="Close candidate preview" onClick={() => candidateDialog.current.close()}><X size={20}/></button></div><p>{job.name}</p><ol>{approved.map(q => <li key={q.id}>{q.text}</li>)}</ol><button className="button secondary" onClick={() => candidateDialog.current.close()}>Back to review</button></dialog>
  </div>;
}
