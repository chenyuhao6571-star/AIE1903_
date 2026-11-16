import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Check, Clock3, FileVideo, LoaderCircle, Play, Plus, Sparkles, Upload, X } from 'lucide-react';
import { jsonRequest, request } from './api';
import useQuestionAnalysis from './useQuestionAnalysis';
import QuestionAnalysisPanel from './QuestionAnalysisPanel';
import InterviewFinalScore from './InterviewFinalScore';
import './videoInterview.css';

const active = record => ['processing', 'running', 'queued', 'extracting', 'transcribing', 'segmenting'].includes(record?.status);
const timeLabel = seconds => {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
};
const segmentRole = segment => ({ question: 'Question', answer: 'Answer', statement: 'Statement' })[segment.qa_role] || '';
const statusLabel = record => {
  if (!record) return '';
  if (record.status === 'completed' || record.status === 'complete') return 'Saved';
  if (['failed', 'interrupted', 'cancelled'].includes(record.status)) return 'Stopped';
  return ({ extracting: 'Extracting audio', transcription: 'Transcribing', transcribing: 'Transcribing', qa: 'Building Q&A', segmenting: 'Building Q&A', queued: 'Queued', uploaded: 'Ready', ready: 'Ready' })[record.stage] || (active(record) ? 'Processing' : 'Ready');
};

export default function VideoInterviewView({ job, onQuestions, initialPanel = null }) {
  const base = `/api/profiles/${job.id}/interviews`;
  const selectedKey = `dossier:interview:${job.id}`;
  const [interviews, setInterviews] = useState([]);
  const [selected, setSelected] = useState(null);
  const [record, setRecord] = useState(null);
  const [samples, setSamples] = useState([]);
  const [loading, setLoading] = useState(true);
  const [samplesLoading, setSamplesLoading] = useState(true);
  const [error, setError] = useState('');
  const [fetchError, setFetchError] = useState('');
  const [samplesError, setSamplesError] = useState('');
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState(initialPanel);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [mediaError, setMediaError] = useState('');
  const [ready, setReady] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [filter, setFilter] = useState('all');
  const [revision, setRevision] = useState(0);
  const analysis = useQuestionAnalysis(job.id, selected);
  const player = useRef();
  const fileInput = useRef();
  const picker = useRef();
  const mounted = useRef(true);
  const actionRunning = useRef(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    request(base).then(rows => {
      if (cancelled) return;
      setInterviews(rows);
      let saved;
      try { saved = localStorage.getItem(selectedKey); } catch { /* Selection is optional. */ }
      setSelected(previous => rows.some(row => row.id === previous) ? previous : rows.find(row => row.id === saved)?.id || rows[0]?.id || null);
      setError('');
    }).catch(e => { if (!cancelled) setError(e.message); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [base, selectedKey, revision]);

  useEffect(() => {
    let cancelled = false;
    setSamplesLoading(true);
    request('/api/interviews/samples').then(rows => { if (!cancelled) { setSamples(rows); setSamplesError(''); } })
      .catch(e => { if (!cancelled) setSamplesError(e.message); })
      .finally(() => { if (!cancelled) setSamplesLoading(false); });
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    setRecord(null); setDuration(0); setCurrentTime(0); setReady(false); setMediaError(''); setFetchError(''); setFilter('all'); setPanel(initialPanel);
    if (!selected) return;
    try { localStorage.setItem(selectedKey, selected); } catch { /* Data is persisted by the server. */ }
    let cancelled = false;
    let timer;
    async function poll() {
      try {
        const detail = await request(`${base}/${selected}`);
        if (cancelled) return;
        setRecord(detail);
        setFetchError('');
        setInterviews(rows => rows.map(row => row.id === detail.id ? { ...row, ...detail } : row));
      } catch (e) { if (!cancelled) setFetchError(e.message); }
      if (!cancelled) timer = setTimeout(poll, 2500);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [base, selected, selectedKey]);

  async function action(run, onSuccess) {
    if (actionRunning.current) return;
    actionRunning.current = true; setBusy(true); setError('');
    try {
      const result = await run();
      if (mounted.current) onSuccess(result);
    } catch (e) { if (mounted.current) setError(e.message); }
    finally { actionRunning.current = false; if (mounted.current) setBusy(false); }
  }
  function acceptInterview(result) {
    setInterviews(rows => [result, ...rows.filter(row => row.id !== result.id)]);
    setSelected(result.id); setRecord(result); picker.current?.close();
  }
  function upload(file) {
    setDragging(false);
    if (!file) return;
    if (!file.size || (!file.type.startsWith('video/') && !/\.(mp4|webm|mov|m4v)$/i.test(file.name))) { setError('Choose a video file.'); return; }
    const form = new FormData(); form.append('file', file);
    action(() => request(base, { method: 'POST', body: form }), acceptInterview);
  }
  function useSample(sample) {
    action(() => request(`${base}/sample`, jsonRequest('POST', { name: sample.name })), acceptInterview);
  }
  function process() {
    const id = selected;
    action(() => request(`${base}/${id}/process`, jsonRequest('POST', { retry: ['failed', 'interrupted', 'cancelled'].includes(record?.status) })), result => {
      if (selectedRef.current === id) setRecord(result);
    });
  }
  function cancel() {
    const id = selected;
    action(() => request(`${base}/${id}/cancel`, jsonRequest('POST', {})), result => {
      if (selectedRef.current === id) setRecord(result);
    });
  }
  function seek(seconds) {
    if (!player.current || !ready) return;
    const target = Math.max(0, Math.min(Number(seconds) || 0, duration || Infinity));
    player.current.currentTime = target; setCurrentTime(target);
  }
  function sampleChoices() {
    return <div className="interview-samples"><h3>Local videos</h3>{samplesLoading ? <p role="status"><LoaderCircle size={16} className="spin"/></p> : samplesError ? <p role="alert" className="error-text">{samplesError}</p> : samples.map(sample => <button className="interview-sample" key={sample.name} disabled={busy} onClick={() => useSample(sample)}><FileVideo size={22}/><span><strong>{sample.name.replace(/\.mp4$/i, '')}</strong><small>{Math.round(sample.size / 1024 / 1024)} MB</small></span><Plus size={17}/></button>)}</div>;
  }
  const qa = record?.qa || [];
  const segments = record?.transcript?.segments || [];
  const activePanel = panel || (record?.has_qa ? 'qa' : 'transcript');
  const visibleQA = qa.filter(item => filter === 'all' || (filter === 'prepared' ? Boolean(item.matched_question_id) : Boolean(item.question) && !item.matched_question_id));
  const mediaBase = selected ? `${base}/${selected}` : '';
  const processing = active(record);
  const progress = record?.progress;
  const progressLabel = typeof progress === 'number' ? `${Math.round(progress)}%` : progress && typeof progress === 'object' && progress.total ? `${progress.completed ?? progress.done ?? 0} / ${progress.total}` : '';
  return <div className="video-interview-view">
    <div className="interview-heading"><h2>Video Interview Analysis</h2>{!!interviews.length && <button className="button secondary" disabled={busy || interviews.some(active) || analysis.running || analysis.busy} onClick={() => picker.current.showModal()}><Plus size={16}/>Add interview</button>}</div>
    <input ref={fileInput} className="file-input" type="file" accept="video/*,.mp4,.webm,.mov" aria-label="Upload interview video" onChange={e => { upload(e.target.files[0]); e.target.value = ''; }}/>
    {(error || fetchError) && <div className="error-banner video-error" role="alert"><span>{error || fetchError}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => { setError(''); setFetchError(''); }}><X size={16}/></button></div>}
    {loading ? <p className="inline-status" role="status"><LoaderCircle size={17} className="spin"/>Loading</p> : !interviews.length ? <div className="interview-upload-area">
      <button className={`interview-dropzone ${dragging ? 'dragging' : ''}`} disabled={busy} onClick={() => fileInput.current.click()} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); if (!busy) upload(e.dataTransfer.files[0]); }}>{busy ? <LoaderCircle size={26} className="spin"/> : <Upload size={26}/>}<strong>{busy ? 'Saving video…' : 'Upload video'}</strong></button>
      {sampleChoices()}
      {error && <button className="text-link" onClick={() => setRevision(n => n + 1)}>Retry</button>}
    </div> : <>
      <div className="interview-video-toolbar"><label>Interview<select aria-label="Interview video" value={selected || ''} disabled={busy} onChange={e => setSelected(e.target.value)}>{interviews.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <div className="interview-actions"><span className="video-process-status" role="status">{processing ? <LoaderCircle size={14} className="spin"/> : record?.has_qa ? <Check size={14}/> : null}{statusLabel(record)}{processing && progressLabel && ` · ${progressLabel}`}</span>
        {record && !record.has_qa && (processing ? <button className="button secondary" disabled={busy} onClick={cancel}>Stop</button> : <button className="button primary" disabled={busy || analysis.running || analysis.busy} onClick={process}>{busy ? <LoaderCircle size={16} className="spin"/> : <Play size={16}/>} {['failed', 'interrupted', 'cancelled'].includes(record.status) ? 'Resume' : record.has_transcript ? 'Build Q&A' : 'Transcribe & build Q&A'}</button>)}
        {record?.has_transcript && <div className="video-analysis-action">
          {!analysis.fresh && analysis.estimate?.estimated_usd != null && <span>≈ ${analysis.estimate.estimated_usd.toFixed(4)}</span>}
          <button className="button primary" disabled={busy || processing || analysis.running || analysis.busy || (!analysis.fresh && !analysis.canRun)} title={analysis.data?.context_error || undefined} onClick={() => { setPanel('analysis'); if (!analysis.fresh) analysis.run(); }}>
            {analysis.running || analysis.busy ? <LoaderCircle size={16} className="spin"/> : <Sparkles size={16}/>}
            {analysis.running || analysis.busy ? 'Analyzing…' : analysis.fresh ? 'View analysis' : ['failed', 'interrupted'].includes(analysis.data?.status) ? 'Retry analysis' : 'Analyze questions'}
          </button>
        </div>}</div>
      </div>
      {record?.error && <p className="error-text" role="alert">{record.error}</p>}
      <div className="interview-review-grid">
        <div className="interview-player-column"><div className="interview-player-shell">{selected && <video key={selected} ref={player} src={`${mediaBase}/video`} controls playsInline preload="metadata" aria-label="Interview video player" onLoadedMetadata={e => { const value = e.currentTarget.duration; setDuration(Number.isFinite(value) ? value : 0); setReady(Number.isFinite(value) && value > 0); }} onTimeUpdate={e => setCurrentTime(e.currentTarget.currentTime)} onError={() => { setMediaError('Video playback failed.'); setReady(false); }}/>}</div>
          {mediaError && <p className="error-text" role="alert">{mediaError}</p>}
          <div className="interview-video-caption"><h3>{record?.name}</h3><span><Clock3 size={14}/>{timeLabel(currentTime)} / {timeLabel(duration)}</span></div>
          <div className="video-downloads">{record?.has_transcript && <a className="text-link" href={`${mediaBase}/transcript.txt`} download><ArrowDownToLine size={14}/>Transcript</a>}{record?.has_audio && <a className="text-link" href={`${mediaBase}/audio`} download><ArrowDownToLine size={14}/>Audio</a>}</div>
          {record?.has_audio && <details className="video-audio"><summary>Audio</summary><audio controls preload="none" src={`${mediaBase}/audio`} aria-label="Interview audio"/></details>}
          {record?.has_transcript && <InterviewFinalScore analysis={analysis} ready={ready} onSeek={seek} onQuestions={onQuestions} disabled={busy || processing} onAnalyze={() => { setPanel('analysis'); analysis.run(); }}/>}
        </div>
        <aside className="interview-answer-panel" aria-label="Interview content"><div className="video-panel-tabs"><button aria-pressed={activePanel === 'qa'} onClick={() => setPanel('qa')}>Questions & answers <span>{qa.length || ''}</span></button><button aria-pressed={activePanel === 'transcript'} onClick={() => setPanel('transcript')}>Transcript <span>{segments.length || ''}</span></button><button aria-pressed={activePanel === 'analysis'} onClick={() => setPanel('analysis')}>Question analysis <span>{analysis.data?.approved_count || ''}</span></button></div>
          {activePanel === 'analysis' ? <QuestionAnalysisPanel analysis={analysis} ready={ready} onSeek={seek} onQuestions={onQuestions}/> : activePanel === 'qa' ? <>
            {!!qa.length && <div className="video-question-filter"><label>Questions<select aria-label="Filter interview questions" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All</option><option value="prepared">Matches Questions</option><option value="additional">Additional</option></select></label></div>}
            <div className="interview-answer-list">{!visibleQA.length ? <p className="video-empty">{qa.length ? 'No matches' : processing ? statusLabel(record) : 'No questions yet'}</p> : visibleQA.map((item, index) => <article className="interview-answer video-qa" key={item.id || index}>
              <div className="interview-answer-meta"><span>{String(qa.indexOf(item) + 1).padStart(2, '0')} · {item.question == null ? 'Statement' : item.matched_question_id ? 'Matches Questions' : 'Additional'}</span><button className="interview-timestamp" disabled={!ready} aria-label={`Seek to question ${qa.indexOf(item) + 1}`} onClick={() => seek(item.question_start ?? item.start)}><Play size={11}/>{timeLabel(item.question_start ?? item.start)}</button></div>
              {item.question && <h3>{item.question}</h3>}
              <div className="video-answer-label"><h4>{item.question ? 'Answer' : 'Statement'}</h4><button className="text-link" disabled={!ready} aria-label={`Seek to answer ${qa.indexOf(item) + 1}`} onClick={() => seek(item.answer_start ?? item.start)}>{timeLabel(item.answer_start ?? item.start)}</button></div><p>{item.answer || '—'}</p>
            </article>)}</div>
          </> : <div className="interview-answer-list video-transcript-list">{!segments.length ? <p className="video-empty">{processing ? statusLabel(record) : 'No transcript yet'}</p> : segments.map((segment, index) => <article className="video-transcript-segment" key={segment.id || index}><div><button className="interview-timestamp" disabled={!ready} onClick={() => seek(segment.start)}><Play size={11}/>{timeLabel(segment.start)}</button>{segmentRole(segment) && <span>{segmentRole(segment)}</span>}{segment.uncertain && <span className="attention">Unclear</span>}</div><p>{segment.text}</p></article>)}</div>}
        </aside>
      </div>
    </>}
    <dialog ref={picker} className="modal interview-candidate-dialog"><div className="modal-heading"><h2>Add interview</h2><button className="icon-button" aria-label="Close interview picker" disabled={busy} onClick={() => picker.current.close()}><X size={20}/></button></div><button className="button primary" disabled={busy} onClick={() => fileInput.current.click()}>{busy ? <LoaderCircle size={16} className="spin"/> : <Upload size={16}/>} {busy ? 'Saving…' : 'Upload video'}</button>{sampleChoices()}{error && <p className="error-text" role="alert">{error}</p>}</dialog>
  </div>;
}
