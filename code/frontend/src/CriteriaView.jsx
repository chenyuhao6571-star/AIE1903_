import { useRef, useState } from 'react';
import { ArrowLeft, ArrowDownToLine, Plus, Upload, X } from 'lucide-react';
import { jsonRequest, request } from './api';

const blankSection = () => ({ id: `section_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`, title: '', max_score: 10, rules: '' });

export function RubricDetails({ rubric }) {
  return <><div className="rubric-list">{rubric.sections.map(section => <div className="rubric-row" key={section.id}>
    <div className="record-heading"><h3>{section.title}</h3><span>{section.max_score} points</span></div>
    <p>{section.rules}</p>
  </div>)}</div><p className="rubric-total">Total<strong>{rubric.sections.reduce((sum, section) => sum + section.max_score, 0)} points</strong></p></>;
}

export default function CriteriaView({ templates, onChanged }) {
  const [detailId, setDetailId] = useState(null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const fileInput = useRef();
  const detail = templates.find(template => template.id === detailId);

  async function save(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const body = { title: editing.title.trim(), sections: editing.sections.map(section => ({ ...section, title: section.title.trim(), max_score: Number(section.max_score), rules: section.rules.trim() })) };
      const saved = await request(editing.id ? `/api/templates/${editing.id}` : '/api/templates', jsonRequest(editing.id ? 'PUT' : 'POST', body));
      await onChanged(); setDetailId(saved.id); setEditing(null);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function importFile(file) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      if (file.size > 100000) throw new Error('Choose a JSON file smaller than 100 KB.');
      let body;
      try { body = JSON.parse((await file.text()).replace(/^\uFEFF/, '')); }
      catch { throw new Error('Could not read the JSON file. Check its format and try again.'); }
      const saved = await request('/api/templates', jsonRequest('POST', body));
      await onChanged(); setDetailId(saved.id);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  const changeSection = (index, changes) => setEditing(current => ({ ...current, sections: current.sections.map((section, i) => i === index ? { ...section, ...changes } : section) }));

  return <div className="workspace library-page">
    {error && <p className="error-text" role="alert">{error}</p>}
    {editing ? <form onSubmit={save} className="criteria-editor">
      <div className="page-heading"><h1>{editing.id ? 'Edit template' : 'New template'}</h1></div>
      <label className="field">Template name<input required maxLength={150} value={editing.title} disabled={busy} onChange={e => setEditing(current => ({ ...current, title: e.target.value }))}/></label>
      {editing.sections.map((section, index) => <fieldset key={section.id} className="editor-section" disabled={busy}>
        <legend>Section {index + 1}</legend>
        <div className="editor-section-fields"><label className="field">Section name<input required maxLength={100} value={section.title} onChange={e => changeSection(index, { title: e.target.value })}/></label>
          <label className="field points-field">Maximum points<input required type="number" min="0.01" max="1000" step="any" value={section.max_score} onChange={e => changeSection(index, { max_score: e.target.value })}/></label>
          <button type="button" className="icon-button remove-section" disabled={editing.sections.length === 1} aria-label={`Remove section ${index + 1}`} onClick={() => setEditing(current => ({ ...current, sections: current.sections.filter((_, i) => i !== index) }))}><X size={17}/></button>
        </div>
        <label className="field">Scoring rules<textarea required minLength={10} maxLength={6000} rows={4} value={section.rules} onChange={e => changeSection(index, { rules: e.target.value })}/></label>
      </fieldset>)}
      <div className="editor-controls"><button type="button" className="button secondary" disabled={busy || editing.sections.length >= 20} onClick={() => setEditing(current => ({ ...current, sections: [...current.sections, blankSection()] }))}><Plus size={16}/>Add section</button><span>Total: {editing.sections.reduce((sum, section) => sum + Number(section.max_score || 0), 0)} points</span></div>
      <div className="editor-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => { setEditing(null); setError(''); }}>Cancel</button><button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save template'}</button></div>
    </form> : detail ? <>
      <button className="text-link back-link" onClick={() => { setDetailId(null); setError(''); }}><ArrowLeft size={15}/>Criteria</button>
      <div className="page-heading"><h1>{detail.title}</h1><div className="actions"><a className="button secondary" href={`/api/templates/${detail.id}/json`}><ArrowDownToLine size={15}/>JSON</a><button className="button primary" onClick={() => setEditing({ id: detail.id, title: detail.title, sections: detail.sections.map(section => ({ ...section })) })}>Edit template</button></div></div>
      <RubricDetails rubric={detail}/>
    </> : <>
      <div className="page-heading"><h1>Criteria</h1><div className="actions"><button className="button secondary" disabled={busy} onClick={() => fileInput.current.click()}><Upload size={15}/>Import JSON</button><button className="button primary" disabled={busy} onClick={() => { setEditing({ title: '', sections: [blankSection()] }); setError(''); }}><Plus size={16}/>New template</button></div></div>
      <div className="table-scroll"><table className="data-table template-table"><thead><tr><th>Template</th><th>Sections</th><th>Maximum score</th></tr></thead><tbody>{templates.map(template => <tr key={template.id}><td><button className="table-link" onClick={() => setDetailId(template.id)}>{template.title}</button></td><td>{template.sections.length}</td><td>{template.max_score}</td></tr>)}</tbody></table></div>
    </>}
    <input ref={fileInput} className="file-input" type="file" accept=".json,application/json" aria-label="Import criteria template" onChange={e => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; }}/>
  </div>;
}
