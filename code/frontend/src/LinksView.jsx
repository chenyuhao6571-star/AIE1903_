import { useEffect, useState } from 'react';
import { ArrowUpRight, LoaderCircle } from 'lucide-react';

export default function LinksView({ job, locked, onImport, onRemove, onSource, analysisAction }) {
  const [url, setUrl] = useState(job.github?.url || '');
  const [importing, setImporting] = useState(false);
  useEffect(() => { setUrl(job.github?.url || ''); }, [job.id, job.github?.url]);
  const github = job.github;
  const detected = job.github_discovery?.candidates || [];
  async function importLink(refresh = false, target = null) {
    setImporting(true);
    try { await onImport(target || (refresh ? github.url : url), refresh); }
    finally { setImporting(false); }
  }
  return <div className="links-view">
    <h2>Links</h2>
    {job.error && <p className="error-text" role="alert">{job.error}</p>}
    {job.github_import_error && <p className="error-text" role="alert">{job.github_import_error}</p>}
    {!github && detected.length === 1 && <div className="github-heading"><a href={detected[0]} target="_blank" rel="noreferrer">{detected[0]}<ArrowUpRight size={14}/></a>{job.github_import_error ? <button className="button secondary" disabled={locked || importing} onClick={() => importLink(false, detected[0])}>{importing ? 'Importing…' : 'Retry import'}</button> : <span className="muted">Found in documents</span>}</div>}
    {!github && detected.length > 1 && <div><p>Multiple GitHub profiles found. Select the candidate’s account.</p>{detected.map(link => <div className="github-heading" key={link}><a href={link} target="_blank" rel="noreferrer">{link}</a><button className="button secondary" disabled={locked || importing} onClick={() => importLink(false, link)}>Use this profile</button></div>)}</div>}
    {!github && !detected.length && <p className="empty-note">{job.documents.length ? 'No GitHub profile link found in the documents.' : 'Upload a CV to find its GitHub link automatically.'}</p>}
    <details className="github-manual"><summary>{github ? 'Change GitHub link' : 'Add GitHub link manually'}</summary><form className="github-form" onSubmit={e => { e.preventDefault(); importLink(); }}>
      <label className="field">GitHub profile<input type="url" required placeholder="https://github.com/username" value={url} onChange={e => setUrl(e.target.value)} disabled={locked} maxLength={300}/></label>
      <button className="button secondary" disabled={locked || importing || !url.trim()}>{importing && <LoaderCircle size={16} className="spin"/>}{importing ? 'Importing…' : 'Import GitHub'}</button>
    </form></details>
    {github && <>
      <div className="github-heading"><a href={github.url} target="_blank" rel="noreferrer">{github.username}<ArrowUpRight size={14}/></a><div className="inline-links"><button className="text-link" disabled={locked} onClick={() => importLink(true)}>Refresh</button><button className="text-link" disabled={locked} onClick={onRemove}>Remove</button></div></div>
      <p className="muted">{github.repositories.length} {github.repositories.length === 1 ? 'repository' : 'repositories'} · Imported {new Date(github.fetched_at).toLocaleString()}</p>
      {!!github.warnings.length && <details><summary>Import limitations</summary><ul>{github.warnings.map((message, i) => <li key={i}>{message}</li>)}</ul></details>}
      {!!job.profile?.links?.length ? job.profile.links.map((link, i) => <article className="record" key={i}><h3>{link.title}</h3><p>{link.description}</p><div className="sources">{link.source_pages.map(id => { const source = job.sources.find(s => s.number === id); return <button key={id} className="text-link" onClick={() => onSource(id)}>{source?.filename || 'Source'}<ArrowUpRight size={13}/></button>; })}</div></article>) : job.profile ? <p className="empty-note">No GitHub analysis was returned.</p> : analysisAction}
      <div className="github-repositories">{github.repositories.map(repo => <article className="record" key={repo.url}><h3><a href={repo.url} target="_blank" rel="noreferrer">{repo.name}<ArrowUpRight size={13}/></a></h3>{repo.description && <p>{repo.description}</p>}<p className="muted">{[repo.language, repo.archived ? 'Archived' : null, !repo.readme_available ? 'README unavailable' : null].filter(Boolean).join(' · ')}</p></article>)}</div>
    </>}
  </div>;
}
