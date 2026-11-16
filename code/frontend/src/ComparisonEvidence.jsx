import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export default function ComparisonEvidence({ row, sectionId, onClose }) {
  const dialog = useRef(null);
  useEffect(() => { dialog.current.showModal(); }, []);
  const score = row.assessment;
  const sections = score?.sections.filter(section => !sectionId || section.section_id === sectionId) || [];
  return <dialog className="source-dialog comparison-evidence" ref={dialog} onClose={onClose}>
    <div className="source-header"><div><h2>{row.name}</h2><p>{row.template_title}</p></div><button className="icon-button" aria-label="Close assessment" onClick={() => dialog.current.close()}><X size={20}/></button></div>
    <div className="source-body">
      {!score ? <p className="empty-note">Assessment details are unavailable. Refresh the comparison to try again.</p> : <>
        <p className="muted">{new Date(score.created_at).toLocaleString()} · Dossier v{score.revision}</p>
        {sections.map(section => <article className="score-row" key={section.section_id}>
          <div className="record-heading"><h3>{section.title}</h3><strong>{section.score} / {section.max_score}</strong></div><p>{section.rationale}</p>
          {section.status === 'insufficient_evidence' && <p className="attention">Incomplete evidence</p>}
          {!!section.gaps?.length && <ul className="gaps">{section.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul>}
          {section.evidence?.map((evidence, i) => {
            const source = score.source_snapshot?.find(source => source.number === evidence.source_id);
            return <blockquote key={i}><p>{evidence.quote}</p>{source ? <details><summary>{source.filename} · {source.unit === 'github' ? 'GitHub' : `${source.unit === 'block' ? 'block' : 'p.'} ${source.page_number}`}</summary><pre className="saved-source">{source.text}</pre></details> : <span className="muted">Source {evidence.source_id} · Saved quote</span>}</blockquote>;
          })}
          <details><summary>Scoring rule</summary><p>{section.rules || score.rubric.sections.find(rule => rule.id === section.section_id)?.rules}</p></details>
        </article>)}
      </>}
    </div>
    <div className="source-footer"><a className="text-link" href={`#candidate/${row.id}/profile`} onClick={onClose}>Open candidate profile</a></div>
  </dialog>;
}
