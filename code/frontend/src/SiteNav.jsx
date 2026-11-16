import { ArrowLeft, ArrowRight } from 'lucide-react';

const links = [
  { id: 'home', label: 'Home' },
  { id: 'cv', label: 'CV analysis' },
  { id: 'interview', label: 'Interview video' },
  { id: 'teaching', label: 'Teaching demo' },
];

export default function SiteNav({ active = '', onNavigate, onBack }) {
  const isHome = active === 'home';
  return (
    <header className="landing-nav">
      <a
        className="landing-brand"
        href="#home"
        onClick={e => { e.preventDefault(); onNavigate('home'); }}
      >
        <span className="landing-brand-mark" aria-hidden="true">AI</span>
        <span>AI Interview Workspace</span>
      </a>
      <nav className="landing-links" aria-label="Main navigation">
        {links.map(link => (
          <button
            key={link.id}
            aria-current={active === link.id ? 'page' : undefined}
            onClick={() => onNavigate(link.id)}
          >
            {link.label}
          </button>
        ))}
      </nav>
      {isHome ? (
        <button className="button primary landing-cta" onClick={() => onNavigate('cv')}>
          Start with a CV
          <ArrowRight size={16} />
        </button>
      ) : (
        <button className="button primary landing-cta" onClick={() => (onBack ? onBack() : onNavigate('home'))}>
          <ArrowLeft size={16} />
          Back
        </button>
      )}
    </header>
  );
}
