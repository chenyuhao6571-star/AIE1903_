import { useState } from 'react';
import { request, jsonRequest } from './api';

export default function CandidateNotes({ job, locked, onSaved }) {
  const [draft, setDraft] = useState(null);
  const [version, setVersion] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  async function save(event) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      await request(`/api/profiles/${job.id}/notes`, jsonRequest('PUT', { version, text: draft }));
      await onSaved(); setDraft(null);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  return <section>
    <div className="section-heading"><h2>Notes</h2>{draft === null && <button className="button secondary" disabled={locked} onClick={() => { setDraft(job.notes || ''); setVersion(job.notes_version || 0); setError(''); }}>{job.notes ? 'Edit notes' : 'Add notes'}</button>}</div>
    {draft === null ? job.notes ? <><p className="candidate-notes">{job.notes}</p><p className="manual-label">Updated {new Date(job.notes_updated_at).toLocaleString()}</p></> : <p className="empty-note">No notes yet.</p>
      : <form onSubmit={save}><label className="field">Your notes<textarea autoFocus className="notes-input" value={draft} maxLength={20000} disabled={saving} onChange={e => setDraft(e.target.value)}/></label>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="editor-actions"><button type="button" className="button secondary" disabled={saving} onClick={() => setDraft(null)}>Cancel</button><button className="button primary" disabled={saving || locked}>{saving ? 'Saving…' : 'Save notes'}</button></div>
      </form>}
  </section>;
}
