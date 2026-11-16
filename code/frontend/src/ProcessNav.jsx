const parts = [
  {
    id: 'dossier',
    title: 'Dossier analysis',
    items: [
      { view: 'documents', label: 'Documents' },
      { view: 'profile', label: 'Profile' },
      { view: 'score', label: 'Score' },
    ],
  },
  {
    id: 'interview',
    title: 'Interview assistance',
    items: [
      { view: 'video', label: 'Interview video' },
      { view: 'questions', label: 'Questions' },
      { view: 'video-clips', label: 'Timeline clips' },
      { view: 'video-answers', label: 'Answer analysis' },
    ],
  },
  {
    id: 'teaching',
    title: 'Teaching demo',
    items: [
      { view: 'teaching', label: 'Teaching demo' },
    ],
  },
];

export default function ProcessNav({ active, onNavigate }) {
  return (
    <nav className="process-nav" aria-label="Recruitment process">
      {parts.map((part, index) => (
        <div className="process-part" key={part.id}>
          <div className="process-part-head">
            <span className="process-part-number">{index + 1}</span>
            <h2>{part.title}</h2>
          </div>
          <div className="process-part-tabs">
            {part.items.map(item => (
              <button
                key={item.view}
                aria-current={active === item.view ? 'page' : undefined}
                onClick={() => onNavigate(item.view)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}
