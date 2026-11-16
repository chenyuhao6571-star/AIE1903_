import { ArrowRight, CircleCheck, Clock3, FileSearch, Presentation, Video } from 'lucide-react';
import SiteNav from './SiteNav.jsx';

const modules = [
  {
    id: 'cv',
    title: 'CV analysis',
    eyebrow: 'Build the evidence base',
    description: 'Extract papers, research outputs, publications and research direction into a source-backed candidate profile you can actually read and reuse.',
    points: ['Papers and publications', 'Research direction', 'Evidence-backed profile'],
    status: 'Ready',
    icon: FileSearch,
  },
  {
    id: 'interview',
    title: 'Interview video',
    eyebrow: 'Run a one-to-one interview',
    description: 'Generate interview questions, record the conversation and analyze answers with timestamped evidence tied back to the candidate profile.',
    points: ['Generated questions', 'Answer analysis', 'Timestamped evidence'],
    status: 'Ready',
    icon: Video,
  },
  {
    id: 'teaching',
    title: 'Teaching demo',
    eyebrow: 'Review classroom delivery',
    description: 'Analyze a recorded teaching demo across lesson content, body language, attitude and facial expression to understand presentation quality.',
    points: ['Lesson content', 'Gestures and attitude', 'Facial expression'],
    status: 'Planned',
    icon: Presentation,
  },
];

export default function HomePage({ onOpenModule, onBack, candidateCount = 0 }) {
  return (
    <div className="landing">
      <SiteNav active="home" onNavigate={onOpenModule} onBack={onBack} />

      <main>
        <section className="landing-hero">
          <div className="landing-hero-copy">
            <p className="landing-eyebrow">AI-assisted interview workspace</p>
            <h1>Turn a candidate dossier into an evidence-backed interview.</h1>
            <p className="landing-subtitle">
              Build a source-backed candidate profile, run and review a recorded interview, and plan
              teaching-demo analysis in one quiet, focused workspace.
            </p>
            <div className="landing-hero-actions">
              <button className="button primary landing-hero-primary" onClick={() => onOpenModule('cv')}>
                Start with a CV
                <ArrowRight size={16} />
              </button>
              <button className="button secondary" onClick={() => onOpenModule('teaching')}>
                Explore teaching demo
              </button>
            </div>
            {candidateCount > 0 && (
              <p className="landing-hero-note">
                {candidateCount} active {candidateCount === 1 ? 'dossier' : 'dossiers'} ready to open
              </p>
            )}
          </div>

          <div className="landing-hero-panel" aria-hidden="true">
            <div className="landing-hero-panel-head">
              <span className="landing-hero-panel-dot" />
              <span>Workspace preview</span>
            </div>
            <div className="landing-hero-panel-body">
              <div className="landing-hero-panel-row">
                <span className="landing-hero-panel-icon"><FileSearch size={18} /></span>
                <div>
                  <strong>CV analysis</strong>
                  <p>Papers, research direction, publications</p>
                </div>
                <CircleCheck size={17} className="landing-hero-panel-check" />
              </div>
              <div className="landing-hero-panel-row">
                <span className="landing-hero-panel-icon"><Video size={18} /></span>
                <div>
                  <strong>Interview video</strong>
                  <p>Generated questions and answer analysis</p>
                </div>
                <CircleCheck size={17} className="landing-hero-panel-check" />
              </div>
              <div className="landing-hero-panel-row is-muted">
                <span className="landing-hero-panel-icon"><Presentation size={18} /></span>
                <div>
                  <strong>Teaching demo</strong>
                  <p>Content, gestures, attitude, expression</p>
                </div>
                <Clock3 size={17} className="landing-hero-panel-clock" />
              </div>
            </div>
          </div>
        </section>

        <section className="landing-modules" aria-labelledby="modules-heading">
          <div className="landing-modules-heading">
            <p className="landing-eyebrow">Modules</p>
            <h2 id="modules-heading">Three clear modules, one workflow</h2>
            <p className="landing-subtitle">
              Each module owns one stage of the interview process, so the tool stays simple and the
              next step is always obvious.
            </p>
          </div>

          <div className="landing-module-grid">
            {modules.map(module => {
              const Icon = module.icon;
              const ready = module.status === 'Ready';
              return (
                <button
                  key={module.id}
                  className="landing-module"
                  onClick={() => onOpenModule(module.id)}
                  type="button"
                >
                  <div className="landing-module-top">
                    <span className="landing-module-icon"><Icon size={22} /></span>
                    <span className={`landing-status ${ready ? 'is-ready' : 'is-planned'}`}>
                      {ready ? <CircleCheck size={13} /> : <Clock3 size={13} />}
                      {module.status}
                    </span>
                  </div>
                  <p className="landing-module-eyebrow">{module.eyebrow}</p>
                  <h3>{module.title}</h3>
                  <p className="landing-module-desc">{module.description}</p>
                  <ul className="landing-module-points">
                    {module.points.map(point => <li key={point}>{point}</li>)}
                  </ul>
                  <span className="landing-module-open">
                    {module.id === 'teaching' ? 'View placeholder' : 'Open module'}
                    <ArrowRight size={15} />
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <span>AI Interview Workspace</span>
        <span>CV analysis · Interview video · Teaching demo</span>
      </footer>
    </div>
  );
}
