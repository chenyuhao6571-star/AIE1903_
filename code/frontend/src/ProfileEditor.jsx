import { useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { request, jsonRequest } from './api';

const fields = {
  bio: ['name', 'current_position', 'institution', 'email', 'website', 'summary', 'research_interests'],
  education: ['degree', 'degree_level', 'degree_status', 'institution', 'department', 'period', 'advisor', 'thesis'],
  work_experience: ['role', 'organization', 'period', 'description'],
  publications: ['title', 'year', 'venue', 'authors', 'authorship', 'status', 'doi', 'url'],
  achievements_awards: ['title', 'year', 'organization', 'description'],
  teaching: ['title', 'description'], research: ['title', 'description'],
  motivation: ['title', 'description'], links: ['title', 'url', 'description'],
};
const required = {
  bio: ['name'], education: ['degree', 'institution'], work_experience: ['role'],
  publications: ['title'], achievements_awards: ['title'], teaching: ['title', 'description'],
  research: ['title', 'description'], motivation: ['title', 'description'], links: ['title', 'url', 'description'],
};
const longFields = ['summary', 'description', 'research_interests', 'authors', 'thesis'];
const label = field => field === 'doi' ? 'DOI' : field === 'url' ? 'URL' : field.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

export default function ProfileEditor({ job, section, title, onClose, onSaved, locked }) {
  // Keep a snapshot across background refreshes; the server rejects stale saves.
  const [snapshot] = useState(() => ({ revision: job.revision, edit_version: job.profile_edit_version }));
  const [records, setRecords] = useState(() => structuredClone(section === 'bio' ? [job.profile.bio] : job.profile[section])
    .map(record => ({ ...record, research_interests: record.research_interests?.join('\n') })));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [vocabulary, setVocabulary] = useState(null);
  const [vocabularyError, setVocabularyError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (section !== 'education') return;
    let cancelled = false;
    setVocabularyError('');
    request('/api/tag-vocabulary').then(value => { if (!cancelled) setVocabulary(value); })
      .catch(e => { if (!cancelled) setVocabularyError(e.message); });
    return () => { cancelled = true; };
  }, [section, retry]);
  function update(index, field, value) {
    setRecords(rows => rows.map((row, i) => i === index ? { ...row, [field]: value, ...(field === 'degree' ? { degree_level: 'Unknown', degree_status: 'Unknown' } : {}) } : row));
  }
  async function save(event) {
    event.preventDefault(); setError('');
    if (records.some(record => !record.source_pages?.length)) {
      setError('Select at least one source for each record.'); return;
    }
    const value = records.map(record => Object.fromEntries([
      ...fields[section].map(field => [field, field === 'research_interests'
        ? (record[field] || '').split('\n').map(text => text.trim()).filter(Boolean)
        : (record[field] || '').trim() || (['degree_level', 'degree_status'].includes(field) ? 'Unknown' : required[section].includes(field) ? '' : null)]),
      ['source_pages', record.source_pages],
    ]));
    setSaving(true);
    try {
      await request(`/api/profiles/${job.id}/profile`, jsonRequest('PUT', { ...snapshot, section, value: section === 'bio' ? value[0] : value }));
      await onSaved(); onClose();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  return <form className="profile-editor" onSubmit={save}>
    <div className="section-heading"><h2>Edit {title.toLowerCase()}</h2><button type="button" className="text-link" disabled={saving} onClick={onClose}>Cancel</button></div>
    <p className="edit-context">Corrections update the profile and tags. Scores use the original documents.</p>
    {section === 'education' && !vocabulary && <p role="status">{vocabularyError || 'Loading tag vocabulary…'}{vocabularyError && <button type="button" className="text-link" onClick={() => setRetry(value => value + 1)}>Retry</button>}</p>}
    <fieldset className="editor-body" disabled={saving || locked || (section === 'education' && !vocabulary)}>
      {records.map((record, index) => <fieldset className="profile-edit-record" key={index}>
        <legend>{section === 'bio' ? 'Profile details' : `Record ${index + 1}`}</legend>
        {section !== 'bio' && <button type="button" className="text-link remove-record" aria-label={`Remove record ${index + 1}`} onClick={() => setRecords(rows => rows.filter((_, i) => i !== index))}><Trash2 size={14}/>Remove record</button>}
        <div className="profile-edit-grid">{fields[section].map(field => <label className={`field ${longFields.includes(field) ? 'wide' : ''}`} key={field}>
          {label(field)}{field === 'research_interests' && ' (one per line)'}{required[section].includes(field) && ' *'}
          {['degree_level', 'degree_status'].includes(field) ? <select value={record[field] || 'Unknown'} onChange={e => update(index, field, e.target.value)}>{(vocabulary?.[field] || ['Unknown']).map(value => <option key={value}>{value}</option>)}</select> : longFields.includes(field) ? <textarea value={record[field] || ''} required={required[section].includes(field)} onChange={e => update(index, field, e.target.value)}/>
            : <input value={record[field] || ''} required={required[section].includes(field)} onChange={e => update(index, field, e.target.value)}/>}
        </label>)}</div>
        <details className="edit-sources"><summary>Sources · {record.source_pages?.length || 0} selected</summary>
          {job.sources.map(source => <label className="source-choice" key={source.number}><input type="checkbox" checked={record.source_pages?.includes(source.number) || false}
            onChange={e => update(index, 'source_pages', e.target.checked ? [...(record.source_pages || []), source.number] : record.source_pages.filter(id => id !== source.number))}/>
            {source.filename}{source.unit !== 'github' && ` · ${source.unit === 'block' ? 'block' : 'p.'} ${source.page_number}`}</label>)}
        </details>
      </fieldset>)}
      {section !== 'bio' && <button type="button" className="button secondary" onClick={() => setRecords(rows => [...rows, { source_pages: [] }])}><Plus size={16}/>Add record</button>}
    </fieldset>
    {error && <p role="alert" className="error-text">{error}</p>}
    <div className="editor-actions"><button type="button" className="button secondary" disabled={saving} onClick={onClose}>Cancel</button><button className="button primary" disabled={saving || locked || (section === 'education' && !vocabulary)}>{saving ? 'Saving…' : 'Save changes'}</button></div>
  </form>;
}
