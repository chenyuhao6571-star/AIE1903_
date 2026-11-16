import { ArrowLeft, ArrowRight, Clock3, Presentation, Sparkles } from 'lucide-react';
import SiteNav from './SiteNav.jsx';

const workflow = [
  { title: 'Upload two synchronized recordings', detail: 'Instructor camera and classroom view from the same lesson.' },
  { title: 'Align timecodes', detail: 'Bring the two feeds onto one shared timeline.' },
  { title: 'Scan presenter actions', detail: 'Detect movement, gestures, speaking and orientation.' },
  { title: 'Review evidence by dimension', detail: 'Jump to timestamped moments grouped by analysis dimension.' },
];

const dimensions = [
  { title: 'Lesson content', detail: 'Structure, clarity, depth and pace of the material.' },
  { title: 'Body language and gestures', detail: 'Movement, hand gestures and physical presence.' },
  { title: 'Attitude and audience orientation', detail: 'Engagement with students and room awareness.' },
  { title: 'Facial expression', detail: 'Expression patterns and how they support delivery.' },
];

export default function TeachingView({ onOpenModule, onBack }) {
  return (
    <div className="landing teaching-page">
      <SiteNav active="teaching" onNavigate={onOpenModule} onBack={onBack} />

      <main className="teaching-main">
        <section className="teaching-hero">
          <span className="landing-status is-planned teaching-badge">
            <Clock3 size={14} />
            Planned · Not implemented
          </span>
          <h1>Teaching demo video analysis</h1>
          <p className="landing-subtitle">
            This module will analyze a recorded teaching demo across lesson content, body language,
            attitude and facial expression. The workflow is designed and the other two modules are
            ready to use today.
          </p>
          <div className="landing-hero-actions">
            <button className="button primary" onClick={() => onOpenModule('cv')}>
              Open CV analysis
              <ArrowRight size={16} />
            </button>
            <button className="button secondary" onClick={() => onOpenModule('home')}>
              <ArrowLeft size={16} />
              Back to home
            </button>
          </div>
        </section>

        <section className="teaching-section" aria-labelledby="workflow-heading">
          <div className="teaching-section-heading">
            <span className="landing-eyebrow">Planned workflow</span>
            <h2 id="workflow-heading">How the analysis will work</h2>
          </div>
          <ol className="teaching-workflow">
            {workflow.map((step, index) => (
              <li key={step.title}>
                <span className="teaching-step-number">{index + 1}</span>
                <div>
                  <h3>{step.title}</h3>
                  <p>{step.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="teaching-section" aria-labelledby="dimensions-heading">
          <div className="teaching-section-heading">
            <span className="landing-eyebrow">Review dimensions</span>
            <h2 id="dimensions-heading">What the analysis will surface</h2>
          </div>
          <div className="teaching-dimensions">
            {dimensions.map(dimension => (
              <article className="teaching-dimension" key={dimension.title}>
                <span className="landing-module-icon"><Presentation size={20} /></span>
                <h3>{dimension.title}</h3>
                <p>{dimension.detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="teaching-note" aria-label="Module status">
          <Sparkles size={18} />
          <p>
            This placeholder keeps the workflow visible. The backend for teaching-demo analysis is
            not part of the current implementation.
          </p>
          <button className="text-link" onClick={() => onOpenModule('cv')}>
            Open the working CV module
            <ArrowRight size={14} />
          </button>
        </section>
      </main>

      <footer className="landing-footer">
        <span>AI Interview Workspace</span>
        <span>Teaching demo · Planned</span>
      </footer>
    </div>
  );
}
