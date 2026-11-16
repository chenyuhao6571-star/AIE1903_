import { useEffect, useRef, useState } from 'react';
import { jsonRequest, request } from './api';

export default function useQuestionAnalysis(jobId, interviewId) {
  const base = interviewId ? `/api/profiles/${jobId}/interviews/${interviewId}/question-analysis` : null;
  const [data, setData] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [estimateError, setEstimateError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const activeBase = useRef(base);
  const pending = useRef(false);
  const epoch = useRef(0);
  activeBase.current = base;
  useEffect(() => { setActionError(''); }, [base]);

  useEffect(() => {
    let cancelled = false;
    let timer;
    epoch.current += 1;
    setData(null); setLoadError('');
    if (!base) return;
    async function poll() {
      const started = epoch.current;
      try {
        const next = await request(base);
        if (!cancelled && started === epoch.current && !pending.current) { setData(next); setLoadError(''); }
      } catch (error) { if (!cancelled) setLoadError(error.message); }
      if (!cancelled) timer = setTimeout(poll, 2500);
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [base, refresh]);

  useEffect(() => {
    let cancelled = false;
    setEstimate(null); setEstimateError('');
    if (base && data?.can_analyze) request(`${base}/estimate`).then(result => { if (!cancelled) setEstimate(result); })
      .catch(error => { if (!cancelled) setEstimateError(error.message); });
    return () => { cancelled = true; };
  }, [base, data?.can_analyze, data?.current_context_hash, refresh]);

  const running = data?.status === 'analyzing';
  const fresh = data?.status === 'completed' && !data?.stale;
  const canRun = Boolean(base && data?.can_analyze && estimate && estimate.context_hash === data.current_context_hash && !running && !busy);
  async function run() {
    if (!canRun || pending.current || fresh) return;
    pending.current = true; epoch.current += 1; setBusy(true); setActionError('');
    try {
      const result = await request(base, jsonRequest('POST', {
        context_hash: estimate.context_hash,
        retry: ['failed', 'interrupted'].includes(data?.status),
      }));
      if (activeBase.current === base) setData(result);
    } catch (error) {
      if (activeBase.current === base) { setActionError(error.message); setRefresh(value => value + 1); }
    } finally { pending.current = false; setBusy(false); }
  }
  return { data, estimate, running, fresh, busy, canRun, run,
    error: actionError || loadError || estimateError || data?.error || '',
    refresh: () => setRefresh(value => value + 1) };
}
