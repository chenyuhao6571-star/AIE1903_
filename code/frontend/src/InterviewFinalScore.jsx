import { useId, useState } from 'react';
import { ArrowRight, ChevronDown, LoaderCircle, Play } from 'lucide-react';
import { criterionLabels, scoreInterview } from './interviewScore';
import './interviewFinalScore.css';

const timestamp = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
};

function ScoredQuestion({ result, score, index, ready, onSeek }) {
  const [expanded, setExpanded] = useState(false);
  const answerId = useId();
  const evidence = result.evidence || [];
  const hasMore = evidence.length > 1 || (evidence[0]?.text.length || 0) > 260;
  return <li className="final-score-question">
    <div className="final-score-question-heading">
      <span className="final-score-number">{String(index + 1).padStart(2, '0')}</span>
      <h4>{result.question}</h4>
      <span className="final-score-question-value" aria-label={`Criterion coverage ${score.score} out of 100`}>{score.score}<small>/100</small></span>
    </div>
    <div className={`final-score-answer ${hasMore && !expanded ? 'is-collapsed' : ''}`} id={answerId}>
      <h5>Answer</h5>
      {!evidence.length ? <p className="final-score-no-answer">Not answered</p> : (expanded ? evidence : evidence.slice(0, 1)).map(item => <div className="final-score-excerpt" key={item.segment_id}>
        <button className="interview-timestamp" disabled={!ready} aria-label={`Seek to answer ${index + 1} at ${timestamp(item.start)}`} onClick={() => onSeek(item.start)}><Play size={11}/>{timestamp(item.start)}</button>
        <p>{item.text}</p>{item.uncertain && <span className="attention">Unclear</span>}
      </div>)}
    </div>
    <div className="final-score-row-actions">{hasMore && <button className="text-link" aria-expanded={expanded} aria-controls={answerId} onClick={() => setExpanded(value => !value)}>{expanded ? 'Show less' : 'Show full answer'}</button>}</div>
    <details className="final-score-reasons">
      <summary>Reasons<ChevronDown size={14}/></summary>
      <div className="final-score-reasons-body">
        <div className="final-score-points">{score.points} / {score.total} points</div>
        {result.criteria.map(criterion => <div className="final-score-criterion" key={criterion.index}>
          <div><h5>{criterion.text}</h5><span className={`analysis-status ${criterion.status}`}>{criterionLabels[criterion.status]}</span></div>
          <p>{criterion.analysis}</p>
          {!!criterion.evidence?.length && <div className="question-analysis-times">{criterion.evidence.map(item => <button className="text-link" key={item.segment_id} disabled={!ready} onClick={() => onSeek(item.start)}>{timestamp(item.start)}</button>)}</div>}
        </div>)}
      </div>
    </details>
  </li>;
}

export default function InterviewFinalScore({ analysis, ready, onSeek, onQuestions, onAnalyze, disabled }) {
  const { data, fresh, running, busy, canRun } = analysis;
  const summary = fresh && !running && !busy ? scoreInterview(data?.results) : null;
  return <section className="interview-final-score" aria-label="Final score">
    <header className="final-score-heading"><div><h3>Final score</h3><span>Criteria coverage</span></div><div className="final-score-value" aria-label={summary ? `Final score ${summary.score} out of 100` : 'Final score unavailable'}>{summary ? summary.score : '—'}<small>/100</small></div></header>
    {summary ? <>
      <div className="final-score-counts"><span>{data.results.length} questions</span><span className="analysis-status found">{summary.counts.found} Covered</span><span className="analysis-status partial">{summary.counts.partial} Partial</span><span className="analysis-status not_found">{summary.counts.not_found} Not answered</span></div>
      <ol className="final-score-list">{data.results.map((result, index) => <ScoredQuestion key={`${data.run_id}:${result.question_id}`} result={result} score={summary.questions[index]} index={index} ready={ready} onSeek={onSeek}/>)}</ol>
    </> : <div className="final-score-empty">
      {running || busy ? <span role="status"><LoaderCircle size={15} className="spin"/>Analyzing…</span>
        : data && !data.approved_count ? <button className="text-link" onClick={onQuestions}>Approve questions<ArrowRight size={14}/></button>
        : <><span>{!data ? 'Loading…' : data.stale ? 'Analysis outdated' : data.status === 'failed' || data.status === 'interrupted' ? 'Analysis unavailable' : 'Not analyzed'}</span><button className="text-link" disabled={disabled || !canRun} onClick={onAnalyze}>{data?.status === 'failed' || data?.status === 'interrupted' ? 'Retry analysis' : 'Analyze questions'}<ArrowRight size={14}/></button></>}
    </div>}
  </section>;
}
