import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, ArrowUpRight, BookOpen, Check, FileText, LoaderCircle, Plus, Search, Upload, X } from 'lucide-react';
import EducationScore from './EducationScore.jsx';

const sections = [
  ['bio', 'Bio'], ['education', 'Education'], ['work_experience', 'Experience'],
  ['achievements_awards', 'Awards'], ['publications', 'Publications'],
  ['score', 'Education Score'],
];
const statusNames = { queued: 'Waiting to process', extracting: 'Reading document', analyzing: 'Extracting profile', failed: 'Needs attention', completed: 'Ready' };
const active = (status) => ['queued', 'extracting', 'analyzing'].includes(status);
function safeUrl(value) {
  if (!value) return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
async function request(url, options) {
  const result = await fetch(url, options);
  if (!result.ok) {
    const data = await result.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not complete the request. Please retry.');
  }
  return result.json();
}

function Sources({ record, onOpen, isWord }) {
  if (!record.source_pages?.length) return null;
  return <div className="sources">{record.source_pages.map(n => <button key={n} onClick={() => onOpen(n)} aria-label={`View source ${isWord ? 'block' : 'page'} ${n}`}>{isWord ? 'Block' : 'p.'} {n}<ArrowUpRight size={12} /></button>)}</div>;
}
function EmptySection() { return <p className="muted section-empty">Not listed in this CV.</p>; }

function ProfileContent({ job, section, onSource }) {
  const p = job.profile;
  const bio = p.bio;
  const isWord = job.extension === '.docx';
  const [query, setQuery] = useState('');
  const source = record => <Sources record={record} onOpen={onSource} isWord={isWord} />;
  if (section === 'bio') return <div className="bio-content">
    <div className="section-heading"><h2>About</h2>{source(bio)}</div>
    {bio.summary ? <p className="summary">{bio.summary}</p> : <EmptySection />}
    {!!bio.research_interests.length && <><h3 className="eyebrow">Research interests</h3><div className="interests">{bio.research_interests.map((s, i) => <span key={i}>{s}</span>)}</div></>}
    <dl className="bio-details">
      {bio.email && <div><dt>Email</dt><dd><a href={`mailto:${encodeURIComponent(bio.email)}`}>{bio.email}</a></dd></div>}
      {safeUrl(bio.website) && <div><dt>Website</dt><dd><a target="_blank" rel="noreferrer" href={safeUrl(bio.website)}>{bio.website.replace(/^https?:\/\//, '').replace(/\/$/, '')}<ArrowUpRight size={14} /></a></dd></div>}
    </dl>
  </div>;
  if (section === 'education') return <><div className="section-heading"><h2>Education</h2><span className="muted">{p.education.length} records</span></div>{!p.education.length && <EmptySection />}<div className="record-list">{p.education.map((r, i) => <article className="record" key={i}><div className="record-aside">{r.period || 'Date not listed'}</div><div className="record-body"><h3>{r.degree}</h3><p className="organization">{r.institution}</p>{r.department && <p className="muted">{r.department}</p>}{r.advisor && <p className="detail"><span>Advisor</span> {r.advisor}</p>}{r.thesis && <p className="detail"><span>Thesis</span> {r.thesis}</p>}{source(r)}</div></article>)}</div></>;
  if (section === 'work_experience') return <><div className="section-heading"><h2>Work experience</h2><span className="muted">{p.work_experience.length} records</span></div>{!p.work_experience.length && <EmptySection />}<div className="record-list">{p.work_experience.map((r, i) => <article className="record" key={i}><div className="record-aside">{r.period || 'Date not listed'}</div><div className="record-body"><h3>{r.role}</h3><p className="organization">{r.organization}</p>{r.description && <p className="muted">{r.description}</p>}{source(r)}</div></article>)}</div></>;
  if (section === 'achievements_awards') return <><div className="section-heading"><h2>Achievements & awards</h2><span className="muted">{p.achievements_awards.length} records</span></div>{!p.achievements_awards.length && <EmptySection />}<div className="record-list">{p.achievements_awards.map((r, i) => <article className="record" key={i}><div className="record-aside">{r.year || 'Year not listed'}</div><div className="record-body"><h3>{r.title}</h3>{r.organization && <p className="organization">{r.organization}</p>}{r.description && <p className="muted">{r.description}</p>}{source(r)}</div></article>)}</div></>;
  const publications = p.publications.filter(r => `${r.title} ${r.venue || ''} ${r.year || ''}`.toLowerCase().includes(query.toLowerCase()));
  return <><div className="section-heading publication-heading"><h2>Publications <span className="heading-count">{p.publications.length}</span></h2><label className="search"><Search size={16}/><input aria-label="Search publications" placeholder="Search papers" value={query} onChange={e => setQuery(e.target.value)} /></label></div>{!publications.length && <p className="muted section-empty">{query ? 'No matching publications.' : 'Not listed in this CV.'}</p>}<div className="paper-list">{publications.map((r, i) => <article className="paper" key={i}><span className="paper-number">{String(i + 1).padStart(2, '0')}</span><div><h3>{r.title}</h3><p className="paper-meta">{[r.venue, r.year, r.authorship, r.status].filter(Boolean).join(' · ')}</p>{r.authors && <details className="authors"><summary>Authors</summary><p>{r.authors}</p></details>}<div className="paper-links">{source(r)}{safeUrl(r.url) && <a href={safeUrl(r.url)} target="_blank" rel="noreferrer">Paper <ArrowUpRight size={12}/></a>}{r.doi && <a href={`https://doi.org/${encodeURIComponent(r.doi.replace(/^https?:\/\/doi.org\//, ''))}`} target="_blank" rel="noreferrer">DOI <ArrowUpRight size={12}/></a>}</div></div></article>)}</div></>;
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [job, setJob] = useState(null);
  const [section, setSection] = useState('bio');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [sampleAvailable, setSampleAvailable] = useState(false);
  const [source, setSource] = useState(null);
  const [sourceError, setSourceError] = useState('');
  const fileInput = useRef();
  const dialog = useRef();
  const selectedRef = useRef(null);
  selectedRef.current = selected;

  async function loadList(initial = false) {
    const rows = await request('/api/profiles');
    setJobs(rows);
    if (initial && rows.length) setSelected(rows[0].id);
  }
  useEffect(() => {
    Promise.all([loadList(true), request('/api/health').then(d => setSampleAvailable(d.sample_available))])
      .catch(e => setError(e.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer;
    setJob(null);
    setSection('bio');
    setSource(null);
    dialog.current?.close();
    if (!selected) return;
    async function poll() {
      try {
        const value = await request(`/api/profiles/${selected}`);
        if (cancelled) return;
        setJob(value);
        setJobs(rows => rows.map(r => r.id === value.id ? { ...r, ...value, profile: undefined } : r));
        if (active(value.status)) timer = setTimeout(poll, 1800);
      } catch (e) { if (!cancelled) { setError(e.message); timer = setTimeout(poll, 5000); } }
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [selected]);

  async function start(file, sample = false) {
    if (busy) return;
    if (file && (!/\.(pdf|docx)$/i.test(file.name) || file.size > 15 * 1024 * 1024 || !file.size)) {
      setError('Choose a non-empty PDF or .docx file smaller than 15 MB.');
      return;
    }
    setBusy(true); setError('');
    try {
      const form = new FormData();
      if (file) form.append('file', file);
      const value = await request(sample ? '/api/sample' : '/api/upload', { method: 'POST', ...(sample ? {} : { body: form }) });
      setJobs(rows => [value, ...rows]); setSelected(value.id);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = ''; }
  }
  async function retry() {
    setBusy(true); setError('');
    try {
      await request(`/api/profiles/${selected}/retry`, { method: 'POST' });
      const id = selected;
      setSelected(null);
      setTimeout(() => setSelected(id), 0);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function openSource(number) {
    const id = selected;
    setSource({ number, text: null }); setSourceError(''); dialog.current.showModal();
    try {
      const pages = await request(`/api/profiles/${id}/text`);
      if (selectedRef.current === id) setSource({ number, text: pages.find(p => p.number === number)?.text || 'Source text unavailable.' });
    } catch (e) { if (selectedRef.current === id) setSourceError(e.message); }
  }
  const profile = job?.profile;
  const uploadButton = <button className="button primary" disabled={busy} onClick={() => fileInput.current.click()}>{busy ? <LoaderCircle size={17} className="spin"/> : <Plus size={17}/>}Upload CV</button>;

  return <div className="app-shell">
    <input ref={fileInput} className="file-input" type="file" accept=".pdf,.docx" aria-label="Upload CV file" onChange={e => e.target.files[0] && start(e.target.files[0])}/>
    <aside className="sidebar">
      <a href="/" className="brand"><span className="brand-mark"><BookOpen size={21}/></span>CV Reader<span className="brand-dot">.</span></a>
      <div className="sidebar-title"><span>Library</span><span>{jobs.length.toString().padStart(2, '0')}</span></div>
      <nav className="candidate-list" aria-label="Uploaded CVs">{jobs.map(j => <button key={j.id} className={`candidate ${selected === j.id ? 'selected' : ''}`} onClick={() => { setSelected(j.id); setError(''); }}><span className="candidate-avatar">{j.name ? j.name.split(' ').filter(Boolean).map(n => n[0]).slice(0, 2).join('') : <FileText size={18}/>}</span><span className="candidate-copy"><strong>{j.name || j.filename}</strong><small>{active(j.status) ? statusNames[j.status] : j.status === 'failed' ? 'Needs attention' : j.institution || j.filename}</small></span>{active(j.status) && <LoaderCircle size={14} className="spin"/>}</button>)}</nav>
      <div className="sidebar-bottom"><span className="small-label">Accepted files</span><span>PDF, DOCX <span className="muted">· up to 15 MB</span></span></div>
    </aside>

    <main className="main">
      <header className="topbar"><span className="breadcrumb"><button onClick={() => { setSelected(null); setJob(null); setError(''); }}>Library</button> <span>/</span> <strong>{selected ? 'Candidate profile' : 'Overview'}</strong></span>{uploadButton}</header>
      {error && <div className="error-banner" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={18}/></button></div>}
      {loading || (selected && !job) ? <div className="center-state" role="status"><LoaderCircle className="spin" size={24}/><p>Loading…</p></div> : !selected ?
        <div className="empty-state"><h1>Upload a CV</h1><div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files[0]) start(e.dataTransfer.files[0]); }}><Upload size={29} strokeWidth={1.4}/><h2>Drop a CV here</h2><p>PDF or Word document · up to 15 MB</p><button disabled={busy} className="button primary" onClick={() => fileInput.current.click()}>Choose file <ArrowRight size={16}/></button></div>{sampleAvailable && <button className="sample-button" disabled={busy} onClick={() => start(null, true)}>Use sample CV <ArrowUpRight size={15}/></button>}</div>
      : active(job.status) ? <div className="processing-state" role="status"><div className="processing-icon"><LoaderCircle size={28} className="spin"/></div><span className="eyebrow">{job.filename}</span><h1>{statusNames[job.status]}</h1><ol className="progress-list">{[['queued', 'File uploaded'], ['extracting', 'Read document'], ['analyzing', 'Build profile']].map(([s, label], i) => { const stage = ['queued', 'extracting', 'analyzing'].indexOf(job.status); return <li key={s} className={i < stage ? 'done' : i === stage ? 'current' : ''}><span>{i < stage ? <Check size={14}/> : i + 1}</span>{label}</li>; })}</ol></div>
      : job.status === 'failed' ? <div className="failure-state"><FileText size={32} strokeWidth={1.3}/><h1>Could not read this CV</h1><p>{job.error}</p><div className="actions"><button className="button primary" disabled={busy} onClick={retry}>Retry</button><button className="button secondary" onClick={() => fileInput.current.click()}>Choose another file</button></div></div>
      : profile && <div className="profile">
        <div className="profile-heading"><div><h1>{profile.bio.name}</h1><p className="profile-subtitle">{[profile.bio.current_position, profile.bio.institution].filter(Boolean).join(' · ')}</p></div><div className="profile-actions"><a className="button secondary" href={`/api/profiles/${job.id}/document`} target="_blank" rel="noreferrer"><FileText size={15}/>Original</a><a className="button secondary" href={`/api/profiles/${job.id}/json`}><ArrowDownToLine size={15}/>JSON</a></div></div>
        <nav className="section-tabs" aria-label="Profile sections">{sections.map(([key, label]) => <button key={key} aria-current={section === key ? 'page' : undefined} onClick={() => setSection(key)} className={section === key ? 'active' : ''}>{label}{key !== 'bio' && key !== 'score' && <span>{profile[key].length}</span>}</button>)}</nav>
        <section className="profile-content" aria-label={sections.find(([key]) => key === section)?.[1]}>{section === 'score' ? <EducationScore candidate={job} onSource={openSource} /> : <ProfileContent job={job} section={section} onSource={openSource}/>}</section>
        <footer className="profile-footer"><FileText size={13}/><span>{job.filename}</span><span>Extracted from CV · not independently verified</span></footer>
      </div>}
    </main>
    <dialog ref={dialog} className="source-dialog" onClick={e => { if (e.target === dialog.current) dialog.current.close(); }}>
      <div className="source-header"><div><span className="eyebrow">Source document</span><h2>{job?.extension === '.docx' ? 'Text block' : 'Page'} {source?.number}</h2></div><button className="icon-button" aria-label="Close source" onClick={() => dialog.current.close()}><X size={21}/></button></div>
      <div className="source-body">{sourceError ? <p role="alert">{sourceError}</p> : source?.text === null ? <p role="status">Loading source…</p> : <pre>{source?.text}</pre>}</div>
      <div className="source-footer"><a className="button secondary" href={`/api/profiles/${selected}/document${job?.extension === '.pdf' ? `#page=${source?.number}` : ''}`} target="_blank" rel="noreferrer">Open original <ArrowUpRight size={15}/></a></div>
    </dialog>
  </div>;
}
