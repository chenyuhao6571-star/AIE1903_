import { ArrowRight, LoaderCircle, Play } from 'lucide-react';
import './questionAnalysis.css';
import { criterionLabels } from './interviewScore';

const statusText = { found: 'Answered', partial: 'Partial answer', not_found: 'No answer' };
const timestamp = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
};

export default function QuestionAnalysisPanel({ analysis, ready, onSeek, onQuestions }) {
  const { data, fresh, running, error } = analysis;
  return <div className="question-analysis-panel" aria-label="Analysis of approved questions">
    {error && <div className="question-analysis-error" role="alert"><p>{error}</p><button className="text-link" onClick={analysis.refresh}>Refresh</button></div>}
    {running || analysis.busy ? <p className="question-analysis-state" role="status"><LoaderCircle size={16} className="spin"/>Analyzing approved questions…</p>
      : fresh ? <div className="interview-answer-list">{data.results.map((result, index) => <article className="question-analysis-result" key={result.question_id}>
        <div className="question-analysis-meta"><span>{String(index + 1).padStart(2, '0')}</span><span className={`analysis-status ${result.status}`}>{statusText[result.status]}</span></div>
        <h3>{result.question}</h3>
        {!!result.evidence.length && <section className="question-analysis-evidence"><h4>Answer excerpts</h4>{result.evidence.map(evidence => <blockquote key={evidence.segment_id}>
          <button className="interview-timestamp" disabled={!ready} onClick={() => onSeek(evidence.start)} aria-label={`Seek to evidence at ${timestamp(evidence.start)}`}><Play size={11}/>{timestamp(evidence.start)}</button>
          <p>{evidence.text}</p>{evidence.uncertain && <span className="attention">Unclear</span>}
        </blockquote>)}</section>}
        <section className="question-analysis-reason"><h4>AI analysis</h4><p>{result.analysis}</p></section>
        <details className="question-analysis-criteria" open><summary>Criteria</summary>{result.criteria.map(criterion => <div className="question-analysis-criterion" key={criterion.index}>
          <div><h5>{criterion.text}</h5><span className={`analysis-status ${criterion.status}`}>{criterionLabels[criterion.status]}</span></div>
          <p>{criterion.analysis}</p>
          {!!criterion.evidence.length && <div className="question-analysis-times">{criterion.evidence.map(evidence => <button className="text-link" key={evidence.segment_id} disabled={!ready} onClick={() => onSeek(evidence.start)}>{timestamp(evidence.start)}</button>)}</div>}
        </div>)}</details>
      </article>)}</div>
      : <div className="question-analysis-state">
        {data?.stale && <p>Questions or transcript changed.</p>}
        {!data?.approved_count ? <button className="text-link" onClick={onQuestions}>Approve questions<ArrowRight size={14}/></button>
          : data?.context_error ? <p>{data.context_error}</p>
          : !error && !data?.stale && <p>{data ? 'Not analyzed' : 'Loading…'}</p>}
      </div>}
  </div>;
}
