import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowUpRight, Check, FileText, LoaderCircle, Plus, Search, Upload, X, SlidersHorizontal, Table2, History, Trash2, Pencil } from 'lucide-react';
import { request, jsonRequest } from './api';
import CriteriaView, { RubricDetails } from './CriteriaView';
import ComparisonView from './ComparisonView';
import HistoryView from './HistoryView';
import LinksView from './LinksView';
import ScoreHistory from './ScoreHistory';
import ProfileEditor from './ProfileEditor';
import TagSearchView from './TagSearchView';
import CandidateNotes from './CandidateNotes';
import InterviewWorkspace from './InterviewWorkspace';
import HomePage from './Home.jsx';
import TeachingView from './TeachingView.jsx';
import SiteNav from './SiteNav.jsx';
import ProcessNav from './ProcessNav.jsx';
import CandidateTeaching from './CandidateTeaching.jsx';
import './home.css';

const profileSections = [['bio', 'Overview'], ['education', 'Education'], ['work_experience', 'Experience'], ['publications', 'Publications'], ['achievements_awards', 'Awards'], ['teaching', 'Teaching'], ['research', 'Research'], ['motivation', 'Motivation'], ['links', 'Links'], ['notes', 'Notes']];
const active = job => ['queued', 'analyzing'].includes(job?.status) || ['queued', 'analyzing'].includes(job?.score_status);
const interviewViews = ['questions', 'video', 'video-clips', 'video-answers'];
const money = value => value == null ? 'Unavailable' : `$${value.toFixed(4)}`;
const postJSON = value => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
function readRoute() {
  const raw = window.location.hash.slice(1);
  if (!raw || raw === 'home' || raw.startsWith('home/')) {
    return { page: 'home', id: null, view: 'documents' };
  }
  const parts = raw.split('/');
  if (parts[0] === 'teaching') {
    return { page: 'teaching', id: null, view: 'documents' };
  }
  return { page: ['criteria', 'comparison', 'history', 'tags'].includes(parts[0]) ? parts[0] : 'candidate',
    id: parts[0] === 'candidate' && /^[a-f0-9]{32}$/.test(parts[1] || '') ? parts[1] : null,
    view: ['documents', 'profile', 'score', 'teaching', ...interviewViews].includes(parts[2]) ? parts[2] : 'documents' };
}
function safeUrl(value) {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function sourceLabel(source) { return source ? source.unit === 'github' ? source.filename : `${source.filename} · ${source.unit === 'block' ? 'block' : 'p.'} ${source.page_number}` : 'Source unavailable'; }
function Sources({ ids = [], job, onOpen }) {
  return <div className="sources">{ids.map(id => <button className="text-link" key={id} onClick={() => onOpen(id)}>{sourceLabel(job.sources.find(s => s.number === id))}<ArrowUpRight size={13}/></button>)}</div>;
}
function Empty({ children }) { return <p className="empty-note">{children || 'Not provided in the uploaded documents.'}</p>; }

function CostAction({ job, operation, busy, onRun }) {
  const [estimate, setEstimate] = useState(null);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let ignore = false;
    setEstimate(null); setError('');
    request(`/api/profiles/${job.id}/estimate?operation=${operation}`).then(data => { if (!ignore) setEstimate(data); })
      .catch(e => { if (!ignore) setError(e.message); });
    return () => { ignore = true; };
  }, [job.id, job.revision, job.rubric_hash, operation, version]);
  const running = operation === 'analysis' ? ['queued', 'analyzing'].includes(job.status) : ['queued', 'analyzing'].includes(job.score_status);
  return <div className="run-area">
    <div className="run-summary">
      {error ? <p role="alert" className="error-text">{error} <button className="text-link" onClick={() => setVersion(v => v + 1)}>Refresh estimate</button></p> : !estimate ? <p className="muted" role="status">Estimating cost…</p> : <p className="cost-line">{estimate.estimated_usd == null ? 'Price unavailable' : <>≈ {money(estimate.estimated_usd)} USD</>}<span aria-hidden="true">·</span>1 request</p>}
    </div>
    <button className="button primary" disabled={busy || active(job) || !estimate} onClick={() => onRun(operation, estimate)}>{running && <LoaderCircle size={16} className="spin"/>}{running ? (operation === 'analysis' ? 'Analyzing…' : 'Scoring…') : (operation === 'analysis' ? 'Analyze dossier' : 'Score candidate')}</button>
  </div>;
}

function ProfileView({ job, onSource, locked, onImport, onRemove, onRun, busy, onSaved }) {
  const [section, setSection] = useState('bio');
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState(false);
  const profile = job.profile;
  const bio = profile?.bio;
  const records = profile?.[section] || [];
  const refs = record => <Sources ids={record.source_pages} job={job} onOpen={onSource}/>;
  if (editing) return <ProfileEditor job={job} section={section} title={profileSections.find(([key]) => key === section)[1]} locked={locked} onSaved={onSaved} onClose={() => setEditing(false)}/>;
  return <div>
    <div className="profile-navigation" aria-label="Profile sections">{profileSections.map(([key, label]) => <button key={key} aria-pressed={section === key} onClick={() => setSection(key)}>{label}</button>)}</div>
    {profile && section !== 'notes' && <div className="profile-edit-toolbar"><span className="manual-label">{job.profile_edited_at ? 'Manually edited' : ''}</span><button className="button secondary" disabled={locked} onClick={() => setEditing(true)}><Pencil size={15}/>Edit section</button></div>}
    {section === 'notes' ? <CandidateNotes job={job} locked={locked} onSaved={onSaved}/> : section === 'links' ? <LinksView job={job} locked={locked} onImport={onImport} onRemove={onRemove} onSource={onSource} analysisAction={<CostAction job={job} operation="analysis" busy={busy} onRun={onRun}/>}/> : !profile ? <Empty>Analyze the dossier to build the candidate’s profile.</Empty> : section === 'bio' ? <div className="overview">
      <h2>Overview</h2>{bio.summary ? <p className="summary">{bio.summary}</p> : <Empty/>}{refs(bio)}
      {!!bio.research_interests.length && <div className="topic-block"><h3>Research interests</h3><p>{bio.research_interests.join(' · ')}</p></div>}
      {(bio.email || safeUrl(bio.website)) && <dl className="contact-list">{bio.email && <div><dt>Email</dt><dd><a href={`mailto:${encodeURIComponent(bio.email)}`}>{bio.email}</a></dd></div>}{safeUrl(bio.website) && <div><dt>Website</dt><dd><a href={safeUrl(bio.website)} target="_blank" rel="noreferrer">{bio.website}<ArrowUpRight size={13}/></a></dd></div>}</dl>}
    </div> : <>
      <div className="section-heading"><h2>{profileSections.find(([key]) => key === section)[1]}</h2>{section === 'publications' && <label className="search"><Search size={16}/><input aria-label="Search publications" placeholder="Search publications" value={query} onChange={e => setQuery(e.target.value)}/></label>}</div>
      {!records.length && <Empty/>}
      {section === 'publications' ? <div>{records.filter(r => `${r.title} ${r.venue || ''} ${r.year || ''}`.toLowerCase().includes(query.toLowerCase())).map((r, i) => <article className="record" key={i}><h3>{r.title}</h3><p className="muted">{[r.venue, r.year, r.status, r.authorship].filter(Boolean).join(' · ')}</p>{r.authors && <details><summary>Authors</summary><p>{r.authors}</p></details>}{refs(r)}<div className="inline-links">{safeUrl(r.url) && <a className="text-link" href={safeUrl(r.url)} target="_blank" rel="noreferrer">Paper<ArrowUpRight size={13}/></a>}{r.doi && <a className="text-link" href={`https://doi.org/${encodeURIComponent(r.doi.replace(/^https?:\/\/doi.org\//, ''))}`} target="_blank" rel="noreferrer">DOI<ArrowUpRight size={13}/></a>}</div></article>)}{!!records.length && !records.some(r => `${r.title} ${r.venue || ''} ${r.year || ''}`.toLowerCase().includes(query.toLowerCase())) && <Empty>No matching publications.</Empty>}</div>
      : records.map((r, i) => <article className="record" key={i}><div className="record-heading"><h3>{r.degree || r.role || r.title}</h3>{(r.period || r.year) && <span className="muted">{r.period || r.year}</span>}</div>{(r.institution || r.organization) && <p>{r.institution || r.organization}</p>}{section === 'education' && <p className="muted">{r.degree_level} · {r.degree_status}</p>}{r.department && <p className="muted">{r.department}</p>}{r.description && <p>{r.description}</p>}{r.advisor && <p>Advisor: {r.advisor}</p>}{r.thesis && <p>Thesis: {r.thesis}</p>}{refs(r)}</article>)}
    </>}
    {profile && <div className="section-footer"><a className="text-link" href={`/api/profiles/${job.id}/json`}><ArrowDownToLine size={15}/>Export profile</a></div>}
  </div>;
}

function ScoreView({ job, templates, busy, onTemplate, onManage, onRun, onSource }) {
  const [tab, setTab] = useState('current');
  const score = job.score;
  return <div>
    <nav className="main-tabs" aria-label="Score views"><button aria-current={tab === 'current' ? 'page' : undefined} onClick={() => setTab('current')}>Current score</button><button aria-current={tab === 'history' ? 'page' : undefined} onClick={() => setTab('history')}>Score history</button></nav>
    {tab === 'history' ? <ScoreHistory job={job}/> : <>
    <div className="section-heading"><h2>{score ? 'Assessment' : 'Score'}</h2><button className="text-link" onClick={onManage}>Manage criteria</button></div>
    <label className="field score-template">Criteria template<select value={job.selected_template_id || ''} disabled={busy || active(job)} onChange={e => onTemplate(e.target.value || null)}>
      <option value="">Choose a template</option>{templates.map(template => <option key={template.id} value={template.id}>{template.title}</option>)}
    </select></label>
    {job.rubric_error && <p className="error-text" role="alert">{job.rubric_error}</p>}
    {score ? <>
      <div className="score-total"><strong>{score.total}<span> / {score.max_score}</span></strong><p>{score.rubric.title}</p></div>
      <div className="score-list">{score.sections.map(row => <article className="score-row" key={row.section_id}><div className="record-heading"><h3>{row.title}</h3><strong>{row.score} / {row.max_score}</strong></div><p>{row.rationale}</p>{row.status === 'insufficient_evidence' && <p className="attention">Incomplete evidence</p>}{!!row.gaps.length && <ul className="gaps">{row.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul>}{row.evidence.map((e, i) => <blockquote key={i}><p>{e.quote}</p><button className="text-link" onClick={() => onSource(e.source_id, e.quote)}>{sourceLabel(job.sources.find(s => s.number === e.source_id))}<ArrowUpRight size={13}/></button></blockquote>)}<details><summary>Scoring rule</summary><p>{row.rules}</p></details></article>)}</div>
      <div className="section-footer"><a className="text-link" href={`/api/profiles/${job.id}/score.json`}><ArrowDownToLine size={15}/>Export assessment</a></div>
    </> : <>
      {job.rubric && <details className="selected-criteria"><summary>View criteria</summary><RubricDetails rubric={job.rubric}/></details>}
      {job.score_status === 'stale' && <p className="attention">Criteria changed. Score again to update.</p>}
      {job.score_error && <p className="error-text" role="alert">{job.score_error}</p>}
      {job.profile && job.rubric ? <CostAction key={job.selected_template_id} job={job} operation="score" busy={busy} onRun={onRun}/> : job.rubric && <Empty>Analyze the dossier first.</Empty>}
    </>}
    </>}
  </div>;
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(() => readRoute().id);
  const [page, setPage] = useState(() => readRoute().page);
  const [templates, setTemplates] = useState([]);
  const [job, setJob] = useState(null);
  const [view, setView] = useState(() => readRoute().view);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sample, setSample] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [candidateName, setCandidateName] = useState('');
  const [candidateQuery, setCandidateQuery] = useState('');
  const [source, setSource] = useState(null);
  const [remove, setRemove] = useState(null);
  const uploadInput = useRef();
  const sourceDialog = useRef();
  const newDialog = useRef();
  const removeDialog = useRef();
  const deleteDialog = useRef();
  const [deleteError, setDeleteError] = useState('');
  const selectedRef = useRef(null);
  const hasNavigatedRef = useRef(false);
  const sourceRequest = useRef(0);
  selectedRef.current = selected;

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [selected, page, view, job?.score?.created_at]);

  function navigate(nextPage, id = selectedRef.current, nextView = 'documents') {
    hasNavigatedRef.current = true;
    setPage(nextPage); setError(''); sourceDialog.current?.close();
    if (nextPage === 'candidate') { setSelected(id); setView(nextView); }
    window.location.hash = nextPage === 'candidate' ? `candidate/${id || ''}/${nextView}` : nextPage;
  }
  function goHome(nextPage = 'home') {
    hasNavigatedRef.current = true;
    setPage(nextPage); setError(''); sourceDialog.current?.close();
    window.location.hash = nextPage === 'home' ? '' : nextPage;
  }
  function goBack() {
    if (hasNavigatedRef.current) {
      window.history.back();
    } else {
      goHome('home');
    }
  }
  function openModule(module) {
    if (module === 'home') return goHome('home');
    if (module === 'teaching') return goHome('teaching');
    if (module === 'interview') return navigate('candidate', jobs[0]?.id || null, jobs[0] ? 'video' : 'documents');
    return navigate('candidate', jobs[0]?.id || null, 'documents');
  }
  useEffect(() => {
    function onHashChange() {
      const route = readRoute();
      setPage(route.page); setError(''); sourceDialog.current?.close();
      if (route.page === 'candidate') { if (route.id) setSelected(route.id); setView(route.view); }
    }
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);
  async function templatesChanged() {
    setTemplates(await request('/api/templates'));
    await refresh();
  }
  async function refresh(id = selectedRef.current) {
    const [rows, detail] = await Promise.all([request('/api/profiles'), id ? request(`/api/profiles/${id}`) : Promise.resolve(null)]);
    setJobs(rows);
    if (id === selectedRef.current) setJob(detail);
    return detail;
  }
  useEffect(() => {
    Promise.all([request('/api/profiles'), request('/api/health'), request('/api/templates')]).then(([rows, health, library]) => {
      setJobs(rows); setSample(health.sample_available); setTemplates(library); if (rows.length && !selectedRef.current) setSelected(rows[0].id);
    }).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    let cancelled = false;
    let timer;
    setJob(null); setError(''); sourceDialog.current?.close();
    if (!selected) return;
    async function poll() {
      try {
        const detail = await request(`/api/profiles/${selected}`);
        if (cancelled) return;
        setJob(previous => previous?.id === detail.id && previous.updated_at > detail.updated_at ? previous : detail);
        setJobs(rows => rows.map(r => r.id === detail.id ? { ...r, name: detail.name, status: detail.status, score_status: detail.score_status, institution: detail.institution } : r));
      } catch (e) { if (!cancelled) setError(e.message); }
      if (!cancelled) timer = setTimeout(poll, 2000);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [selected]);

  async function perform(action, after) {
    if (busy) return;
    const id = selectedRef.current;
    setBusy(true); setError('');
    try { await action(); const result = await refresh(id); if (id === selectedRef.current) after?.(result); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function createCandidate(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const created = await request('/api/profiles', postJSON({ name: candidateName }));
      setJobs(rows => [created, ...rows]); navigate('candidate', created.id); setCandidateName(''); setCandidateQuery(''); newDialog.current.close();
    } catch (e) { setError(e.message); newDialog.current.close(); }
    finally { setBusy(false); }
  }
  async function upload(files) {
    if (!files.length || !job || active(job)) return;
    const form = new FormData();
    for (const file of files) form.append('files', file);
    await perform(() => request(`/api/profiles/${job.id}/documents`, { method: 'POST', body: form }));
  }
  async function run(operation, estimate) {
    await perform(() => request(`/api/profiles/${job.id}/${operation === 'analysis' ? 'analyze' : 'score'}`, postJSON({ revision: estimate.revision, rubric_hash: estimate.rubric_hash })));
  }
  async function chooseTemplate(templateId) {
    await perform(() => request(`/api/profiles/${job.id}/template`, jsonRequest('PUT', { template_id: templateId })));
  }
  async function openSource(number, quote = null) {
    const id = job.id;
    const token = ++sourceRequest.current;
    const meta = job.sources.find(s => s.number === number);
    setSource({ ...meta, quote, text: null, error: null }); sourceDialog.current.showModal();
    try {
      const pages = await request(`/api/profiles/${id}/text`);
      if (selectedRef.current === id && sourceRequest.current === token) setSource({ ...pages.find(p => p.number === number), quote });
    } catch (e) { if (sourceRequest.current === token) setSource(s => ({ ...s, error: e.message })); }
  }
  async function deleteCandidate() {
    if (busy) return;
    const id = job.id;
    setBusy(true); setDeleteError('');
    try {
      await request(`/api/profiles/${id}`, { method: 'DELETE' });
      const rows = jobs.filter(row => row.id !== id);
      setJobs(rows); setJob(null); setSelected(null);
      deleteDialog.current.close();
      navigate('candidate', rows[0]?.id || null);
    } catch (e) { setDeleteError(e.message); }
    finally { setBusy(false); }
  }
  const locked = busy || active(job);
  const visibleJobs = jobs.filter(j => !candidateQuery.trim() || j.name.toLocaleLowerCase().includes(candidateQuery.trim().toLocaleLowerCase()));
  if (page === 'home') {
    return <HomePage onOpenModule={openModule} onBack={goBack} candidateCount={jobs.length} />;
  }
  if (page === 'teaching') {
    return <TeachingView onOpenModule={openModule} onBack={goBack} />;
  }
  const navActive = page === 'candidate' ? (view === 'teaching' ? 'teaching' : interviewViews.includes(view) ? 'interview' : 'cv') : '';
  return <div className="app-frame">
    <div className="app-frame-nav"><SiteNav active={navActive} onNavigate={openModule} onBack={goBack} /></div>
    <div className="app-shell">
    <aside className="sidebar">
      <nav className="workspace-nav" aria-label="Workspace">
        <button aria-current={page === 'tags' ? 'page' : undefined} disabled={busy} onClick={() => navigate('tags')}><Search size={17}/>Tag search</button>
        <button aria-current={page === 'criteria' ? 'page' : undefined} disabled={busy} onClick={() => navigate('criteria')}><SlidersHorizontal size={17}/>Criteria</button>
        <button aria-current={page === 'comparison' ? 'page' : undefined} disabled={busy} onClick={() => navigate('comparison')}><Table2 size={17}/>Comparison</button>
        <button aria-current={page === 'history' ? 'page' : undefined} disabled={busy} onClick={() => navigate('history')}><History size={17}/>Request history</button>
      </nav>
      <div className="sidebar-heading"><h2>Candidates</h2><button className="icon-button" aria-label="New candidate" disabled={busy} onClick={() => newDialog.current.showModal()}><Plus size={19}/></button></div>
      <label className="candidate-search">
        <Search size={15}/>
        <input value={candidateQuery} onChange={e => setCandidateQuery(e.target.value)} placeholder="Search candidates" aria-label="Search candidates" autoComplete="off"/>
        {candidateQuery && <button className="candidate-search-clear" type="button" aria-label="Clear search" onClick={() => setCandidateQuery('')}><X size={14}/></button>}
      </label>
      <nav aria-label="Candidates" className="candidate-list">{visibleJobs.map(j => <button key={j.id} className={`candidate ${page === 'candidate' && selected === j.id ? 'selected' : ''}`} aria-current={page === 'candidate' && selected === j.id ? 'page' : undefined} disabled={busy} onClick={() => navigate('candidate', j.id)}><span><strong>{j.name}</strong></span>{active(j) && <LoaderCircle size={15} className="spin"/>}</button>)}</nav>
      {!visibleJobs.length && <p className="candidate-empty">{candidateQuery ? `No matches for "${candidateQuery}"` : 'No candidates yet'}</p>}
    </aside>
    <main className="main">
      {error && <div className="error-banner" role="alert"><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={18}/></button></div>}
      {loading ? <div className="center-state" role="status">Loading…</div> : page === 'criteria' ? <CriteriaView templates={templates} onChanged={templatesChanged}/> : page === 'comparison' ? <ComparisonView templates={templates}/> : page === 'tags' ? <TagSearchView onOpen={id => navigate('candidate', id, 'profile')}/> : page === 'history' ? <HistoryView/> : selected && !job && !error ? <div className="center-state" role="status"><LoaderCircle size={24} className="spin"/><p>Loading dossier…</p></div> : !job ? <div className="welcome"><h1>Candidates</h1><button className="button primary" onClick={() => newDialog.current.showModal()}><Plus size={17}/>New candidate</button></div> : <div className={`workspace ${interviewViews.includes(view) ? 'interview-layout' : ''}`}>
        <header className="candidate-header"><div className="section-heading"><h1>{job.name}</h1><button className="icon-button" aria-label="Delete candidate" disabled={locked} onClick={() => { setDeleteError(''); deleteDialog.current.showModal(); }}><Trash2 size={18}/></button></div>{job.institution && <p className="muted">{[job.profile?.bio.current_position, job.institution].filter(Boolean).join(' · ')}</p>}</header>
        <ProcessNav active={view} onNavigate={key => navigate('candidate', job.id, key)}/>
        <section className="view" aria-label={view}>
          {interviewViews.includes(view) ? <InterviewWorkspace key={`${job.id}-${view}`} job={job} view={view} onSource={openSource} onNavigate={nextView => navigate('candidate', job.id, nextView)}/> : view === 'teaching' ? <CandidateTeaching job={job} onBack={() => navigate('candidate', job.id, 'documents')}/> : view === 'documents' ? <>
            <div className="section-heading"><h2>Candidate documents</h2><button className="button secondary" disabled={locked} onClick={() => uploadInput.current.click()}><Plus size={16}/>Add files</button></div>
            <input ref={uploadInput} className="file-input" type="file" multiple accept=".pdf,.docx" aria-label="Upload candidate documents" onChange={e => { upload(Array.from(e.target.files)); e.target.value = ''; }}/>
            <div className={`document-area ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); if (!locked) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!locked) upload(Array.from(e.dataTransfer.files)); }}>
              {job.documents.length ? <ul className="document-list">{job.documents.map(doc => <li key={doc.id}><FileText size={21}/><div className="document-info"><a href={`/api/profiles/${job.id}/documents/${doc.id}`} target="_blank" rel="noreferrer">{doc.filename}<ArrowUpRight size={13}/></a><span>{doc.extension.slice(1).toUpperCase()} · {doc.source_count} {doc.extension === '.docx' ? 'text blocks' : 'pages'} · {Math.max(1, Math.round(doc.size / 1024))} KB</span></div><button className="icon-button" disabled={locked} aria-label={`Remove ${doc.filename}`} onClick={() => { setRemove(doc); removeDialog.current.showModal(); }}><X size={17}/></button></li>)}</ul> : <button className="dropzone" disabled={locked} onClick={() => uploadInput.current.click()}><Upload size={24}/><strong>Choose files or drop them here</strong></button>}
            </div>
            <div className="document-footer">{sample && !job.documents.length && <button className="text-link" disabled={locked} onClick={() => perform(() => request(`/api/profiles/${job.id}/sample`, { method: 'POST' }))}>Add sample CV</button>}</div>
            {busy && <p className="inline-status" role="status"><LoaderCircle className="spin" size={16}/>Saving changes…</p>}
            {job.error && <p className="error-text" role="alert">{job.error}</p>}
            {job.github_import_error && <p className="attention" role="alert">GitHub: {job.github_import_error}</p>}
            {job.profile ? <div className="ready-row"><span><Check size={17}/>Dossier analyzed</span><button className="button primary" onClick={() => navigate('candidate', job.id, 'profile')}>View profile</button></div> : (!!job.documents.length || job.github) && <CostAction job={job} operation="analysis" busy={busy} onRun={run}/>}
          </> : view === 'profile' ? <ProfileView key={job.id} onSaved={() => refresh(job.id)} job={job} onSource={openSource} locked={locked} busy={busy} onRun={run} onImport={(url, refresh) => perform(() => request(`/api/profiles/${job.id}/github`, jsonRequest('PUT', { url, refresh })))} onRemove={() => perform(() => request(`/api/profiles/${job.id}/github`, { method: 'DELETE' }))}/> : <ScoreView key={job.id} job={job} templates={templates} busy={busy} onTemplate={chooseTemplate} onManage={() => navigate('criteria')} onRun={run} onSource={openSource}/>}
        </section>
      </div>}
    </main>
    <dialog ref={deleteDialog} className="modal"><h2>Delete candidate?</h2><p className="filename">{job?.name}</p><p>This permanently deletes the candidate, documents, profile, notes, scores and request history.</p>{deleteError && <p className="error-text" role="alert">{deleteError}</p>}<div className="modal-actions"><button className="button secondary" disabled={busy} onClick={() => deleteDialog.current.close()}>Cancel</button><button className="button danger" disabled={locked} onClick={deleteCandidate}>{busy ? 'Deleting…' : 'Delete candidate'}</button></div></dialog>
    <dialog ref={newDialog} className="modal"><form onSubmit={createCandidate}><div className="modal-heading"><h2>New candidate</h2><button className="icon-button" type="button" aria-label="Close new candidate" onClick={() => newDialog.current.close()}><X size={19}/></button></div><label className="field">Candidate name<input autoFocus value={candidateName} onChange={e => setCandidateName(e.target.value)} maxLength={150} placeholder="Name or application reference"/></label><div className="modal-actions"><button type="button" className="button secondary" onClick={() => newDialog.current.close()}>Cancel</button><button className="button primary" disabled={busy}>Create dossier</button></div></form></dialog>
    <dialog ref={removeDialog} className="modal"><div className="modal-heading"><h2>Remove document?</h2><button className="icon-button" aria-label="Cancel document removal" onClick={() => removeDialog.current.close()}><X size={19}/></button></div><p className="filename">{remove?.filename}</p><p>This removes the document from this dossier.{job?.profile ? ' The profile and assessment will need to be generated again.' : ''}</p><div className="modal-actions"><button className="button secondary" onClick={() => removeDialog.current.close()}>Cancel</button><button className="button danger" disabled={locked} onClick={() => { const id = remove.id; removeDialog.current.close(); perform(() => request(`/api/profiles/${job.id}/documents/${id}`, { method: 'DELETE' })); }}>Remove</button></div></dialog>
    <dialog ref={sourceDialog} className="source-dialog"><div className="source-header"><div><h2>Source</h2><p>{sourceLabel(source)}</p></div><button className="icon-button" aria-label="Close source" onClick={() => sourceDialog.current.close()}><X size={20}/></button></div><div className="source-body">{source?.quote && <blockquote><p>{source.quote}</p></blockquote>}{source?.error ? <p role="alert" className="error-text">{source.error}</p> : source?.text == null ? <p role="status">Loading source…</p> : <pre>{source.text}</pre>}</div>{(source?.document_id || safeUrl(source?.source_url)) && <div className="source-footer"><a className="button secondary" href={safeUrl(source.source_url) || `/api/profiles/${job?.id}/documents/${source.document_id}${source.unit === 'page' ? `#page=${source.page_number}` : ''}`} target="_blank" rel="noreferrer">Open original<ArrowUpRight size={15}/></a></div>}</dialog>
  </div>
  </div>;
}
