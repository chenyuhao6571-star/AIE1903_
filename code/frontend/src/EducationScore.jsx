import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Check, ClipboardList, ExternalLink, FileText, Gavel, LoaderCircle, Scale, ShieldAlert, X } from 'lucide-react';

const COMPONENT_ORDER = [
  'degree_level',
  'field_relevance',
  'overall_university_strength',
  'subject_university_strength',
  'dissertation_relevance',
  'academic_progression',
  'academic_honors',
  'additional_relevant_education',
];

const QUALITY_META = { HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low', UNKNOWN: 'Unknown' };
const STATUS_META = { MET: 'Met', 'NOT MET': 'Not met', PARTIAL: 'Partial', UNKNOWN: 'Unknown' };
const REVIEW_META = { PENDING: 'Pending review', REVIEWED: 'Reviewed' };

async function requestJson(url, options) {
  const result = await fetch(url, options);
  const data = await result.json().catch(() => ({}));
  if (!result.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not complete the request. Please retry.');
  return data;
}

function fmtScore(value) {
  if (value === null || value === undefined) return '\u2014';
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : '\u2014';
}

function EvidenceList({ evidence, onSource }) {
  if (!evidence?.length) return <p className="muted report-empty">No evidence listed.</p>;
  return <ul className="evidence-list">{evidence.map((e, i) => {
    const pages = (e.source_location || '').split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isFinite(n));
    return <li key={e.evidence_id || `${e.claim}-${i}`} className="evidence-item">
      <div className="evidence-head">
        <span className={`evidence-tag ${e.source_type === 'CV' ? 'cv' : 'external'}`}>{e.source_type}</span>
        <span className="evidence-claim">{e.claim}</span>
        {e.confidence != null && <span className="evidence-confidence">{(e.confidence * 100).toFixed(0)}%</span>}
      </div>
      <div className="evidence-meta">
        {e.source_location && <span>Source: {e.source_location}</span>}
        {e.ranking_year && <span>Year: {e.ranking_year}</span>}
        {e.retrieved_at && <span>Retrieved: {e.retrieved_at}</span>}
        {e.verified && <span className="verified"><Check size={12} /> verified</span>}
      </div>
      <div className="evidence-actions">
        {pages.length > 0 && pages.map(p => <button key={p} className="link-button" onClick={() => onSource(p)}>View source p.{p} <ArrowUpRight size={12} /></button>)}
        {e.source_url && <a className="link-button" href={e.source_url} target="_blank" rel="noreferrer">Open external source <ExternalLink size={12} /></a>}
      </div>
    </li>;
  })}</ul>;
}

function ComponentCard({ component, onReview, editing, onStartReview, onCancelReview, onSource }) {
  const [open, setOpen] = useState(false);
  const [score, setScore] = useState('');
  const [reason, setReason] = useState('');
  const scoreMissing = component.score === null || component.score === undefined;
  useEffect(() => {
    if (editing) {
      setScore(component.score != null ? String(component.score) : '');
      setReason('');
    }
  }, [editing, component.score]);
  return <article className="component-card">
    <header className="component-head" onClick={() => setOpen(v => !v)}>
      <div className="component-title">
        <span className="component-score">{scoreMissing ? '\u2014' : fmtScore(component.score)}</span>
        <div>
          <h3>{component.label}</h3>
          <p className="component-sub">Weight {Math.round(component.weight * 100)}% <span className="dot">\u00b7</span> {component.status}</p>
        </div>
      </div>
      <div className="component-toggle">{open ? '\u2013' : '+'}</div>
    </header>
    {open && <div className="component-body">
      <p className="component-explanation">{component.explanation}</p>
      {component.confidence != null && <p className="muted">Evidence confidence: {(component.confidence * 100).toFixed(0)}%</p>}
      <h4 className="report-eyebrow">Evidence</h4>
      <EvidenceList evidence={component.evidence} onSource={onSource} />
      <div className="review-box">
        <h4 className="report-eyebrow"><Gavel size={13} /> Reviewer override</h4>
        {editing ? <>
          <div className="review-fields">
            <input aria-label="Reviewer score" type="number" min="0" max="10" step="0.1" placeholder="0\u201310" value={score} onChange={e => setScore(e.target.value)} />
            <input aria-label="Reason" type="text" placeholder="Reason (optional)" value={reason} onChange={e => setReason(e.target.value)} />
          </div>
          <div className="review-actions">
            <button className="button primary" disabled={score === ''} onClick={() => onReview(component.name, parseFloat(score), reason)}>Save override</button>
            <button className="button secondary" onClick={onCancelReview}>Cancel</button>
          </div>
        </> : <>
          <p className="muted">Override a component score here. The original score is preserved in the audit trail.</p>
          <button className="button secondary" onClick={onStartReview}>Override score</button>
        </>}
      </div>
    </div>}
  </article>;
}

export default function EducationScore({ candidate, onSource }) {
  const [assessment, setAssessment] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [jobId, setJobId] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [jobText, setJobText] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editingComponent, setEditingComponent] = useState(null);
  const candidateId = candidate?.id;

  const load = useCallback(async () => {
    if (!candidateId) return;
    setLoading(true);
    try {
      const [jobList, existing] = await Promise.all([
        requestJson('/api/jobs').catch(() => []),
        requestJson(`/api/education/assessment/${candidateId}`).catch(() => null),
      ]);
      setJobs(Array.isArray(jobList) ? jobList : []);
      setAssessment(existing);
      if (!jobId && Array.isArray(jobList) && jobList.length) setJobId(jobList[0].id);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [candidateId, jobId]);

  useEffect(() => { load(); }, [load]);

  const createJob = useCallback(async () => {
    if (!jobText.trim()) { setError('Enter a job description first.'); return; }
    setBusy(true); setError('');
    try {
      const created = await requestJson('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: jobText, title: jobTitle.trim() || undefined }),
      });
      setJobs(rows => [created, ...rows.filter(r => r.id !== created.id)]);
      setJobId(created.id);
      setJobTitle(''); setJobText('');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [jobText, jobTitle]);

  const analyze = useCallback(async () => {
    if (!jobId) { setError('Choose or create a job description first.'); return; }
    setBusy(true); setError('');
    try {
      const result = await requestJson(`/api/education/analyze/${candidateId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId }),
      });
      setAssessment(result);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [candidateId, jobId]);

  const review = useCallback(async (component, reviewerScore, reason) => {
    setBusy(true); setError('');
    try {
      const result = await requestJson(`/api/education/review/${candidateId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ component, reviewer_score: reviewerScore, reason: reason || undefined, version: assessment?.version }),
      });
      setAssessment(result);
      setEditingComponent(null);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, [candidateId]);

  const components = useMemo(() => {
    const map = assessment?.education_score?.components || {};
    return COMPONENT_ORDER.filter(name => map[name]).map(name => map[name]);
  }, [assessment]);

  const qc = assessment?.qualification_check;
  const score = assessment?.education_score;
  const hasAssessment = !!assessment;

  return <div className="score-report">
    <div className="score-report-head">
      <div>
        <h2>Education Background Assessment</h2>
        <p className="muted">Evidence-based score for the selected position. Reviewers can override individual components.</p>
      </div>
      {hasAssessment && <div className="score-badges">
        <span className={`badge badge-${qc?.status?.replace(/\s+/g, '-').toLowerCase() || 'unknown'}`}><ShieldAlert size={13} /> {STATUS_META[qc?.status] || qc?.status || 'Unknown'}</span>
        <span className={`badge badge-${assessment?.review_status?.toLowerCase()}`}>{REVIEW_META[assessment?.review_status] || assessment?.review_status}</span>
      </div>}
    </div>

    {error && <div className="error-banner report-error" role="alert"><span>{error}</span><button aria-label="Dismiss" onClick={() => setError('')}><X size={18} /></button></div>}

    <section className="report-setup">
      <div className="setup-fields">
        <input className="text-input" aria-label="Job title" placeholder="Job title (e.g. Assistant Professor in AI)" value={jobTitle} onChange={e => setJobTitle(e.target.value)} />
        <select className="text-input" aria-label="Existing job description" value={jobId} onChange={e => setJobId(e.target.value)}>
          <option value="">Choose an existing job description\u2026</option>
          {jobs.map(j => <option key={j.id} value={j.id}>{j.title || j.filename}</option>)}
        </select>
      </div>
      <textarea className="text-input job-textarea" aria-label="Job description" placeholder="Or paste a new job description here\u2026 (e.g. \u201cApplicants must hold a PhD in Computer Science\u2026\u201d)" value={jobText} onChange={e => setJobText(e.target.value)} rows={3} />
      <div className="setup-actions">
        <button className="button secondary" disabled={busy || !jobText.trim()} onClick={createJob}>Create job</button>
        <button className="button primary" disabled={busy || !jobId} onClick={analyze}>{busy ? <LoaderCircle size={16} className="spin" /> : <Scale size={16} />}Run education analysis</button>
      </div>
    </section>

    {loading ? <div className="center-state compact" role="status"><LoaderCircle size={22} className="spin" /><p>Loading assessment\u2026</p></div> : !hasAssessment ? <div className="report-blank">
      <ClipboardList size={30} strokeWidth={1.4} />
      <h3>No assessment yet</h3>
      <p className="muted">Choose or paste a job description above, then run the analysis to produce a report.</p>
    </div> : <>
      <section className="score-summary">
        <div className="final-score">
          <span className="final-score-number">{fmtScore(score?.final_score)}</span>
          <span className="final-score-label">Final Education Score <span className="muted">/ 10</span></span>
        </div>
        <div className="summary-meta">
          <div><span>Evidence quality</span><strong>{QUALITY_META[assessment.evidence_quality] || assessment.evidence_quality}</strong></div>
          <div><span>Position</span><strong>{assessment.position_title || '\u2014'}</strong></div>
          <div><span>Rubric</span><strong>{assessment.rubric_version}</strong></div>
            {assessment.version != null && <div><span>Version</span><strong>v{assessment.version}</strong></div>}
          {score?.external_evidence_incomplete && <div className="summary-warning"><ShieldAlert size={14} /> External ranking evidence is incomplete.</div>}
        </div>
      </section>

      <section className="report-section">
        <h3 className="report-section-title"><ShieldAlert size={16} /> Minimum Qualification</h3>
        <div className="qualification-card">
          <p>{qc?.explanation}</p>
          <div className="qualification-flags">
            <span className="flag"><Check size={13} /> Minimum degree {qc?.minimum_degree_met == null ? 'not stated' : qc.minimum_degree_met ? 'met' : 'not met'}</span>
            <span className="flag"><Check size={13} /> Field requirement {qc?.field_requirement_met == null ? 'not stated' : qc.field_requirement_met ? 'met' : 'not met'}</span>
            {qc?.in_progress_degree && <span className="flag"><X size={13} /> In progress: {qc.in_progress_degree} (not yet awarded)</span>}
            {qc?.preferred_degree && <span className="flag"><ArrowUpRight size={13} /> Preferred degree: {qc.preferred_degree}</span>}
          </div>
          <h4 className="report-eyebrow">Evidence</h4>
          <EvidenceList evidence={qc?.evidence} onSource={onSource} />
        </div>
      </section>

      <section className="report-section">
        <h3 className="report-section-title"><Scale size={16} /> Component Scores</h3>
        {components.map(c => <ComponentCard key={c.name} component={c} onSource={onSource} onReview={review} editing={editingComponent === c.name} onStartReview={() => setEditingComponent(c.name)} onCancelReview={() => setEditingComponent(null)} />)}
      </section>

      {score?.calculation?.length > 0 && <section className="report-section">
        <h3 className="report-section-title">Calculation</h3>
        <table className="calc-table">
          <thead><tr><th>Component</th><th>Score</th><th>Weight</th><th>Contribution</th></tr></thead>
          <tbody>{score.calculation.map((line, i) => <tr key={`${line.component}-${i}`}>
            <td>{components.find(c => c.name === line.component)?.label || line.component}</td>
            <td>{fmtScore(line.score)}</td>
            <td>{Math.round(line.weight * 100)}%</td>
            <td>{fmtScore(line.contribution)}</td>
          </tr>)}</tbody>
        </table>
      </section>}

      {assessment.missing_information?.length > 0 && <section className="report-section">
        <h3 className="report-section-title">Missing Information</h3>
        <ul className="missing-list">{assessment.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul>
      </section>}

      {assessment.audit_trail?.length > 0 && <section className="report-section">
        <h3 className="report-section-title"><FileText size={16} /> Audit Trail</h3>
        <ul className="audit-list">{assessment.audit_trail.map((a, i) => <li key={i}>
          <span className="audit-time">{a.timestamp?.slice(0, 19).replace('T', ' ')}</span>
          <strong>{a.component}</strong> {a.original_score != null && <span>from {fmtScore(a.original_score)}</span>} {a.reviewer_score != null && <span>\u2192 {fmtScore(a.reviewer_score)}</span>}
          {a.reason && <span className="muted">\u2014 {a.reason}</span>}
        </li>)}</ul>
      </section>}
    </>}
  </div>;
}
