import { ArrowLeft, Clock3, Presentation } from 'lucide-react';

const dimensions = [
  { title: 'Lesson content', detail: 'Structure, clarity, depth and pace of the material.' },
  { title: 'Body language and gestures', detail: 'Movement, hand gestures and physical presence.' },
  { title: 'Attitude and audience orientation', detail: 'Engagement with students and room awareness.' },
  { title: 'Facial expression', detail: 'Expression patterns and how they support delivery.' },
];

export default function CandidateTeaching({ job, onBack }) {
  return (
    <div className="candidate-teaching">
      <span className="landing-status is-planned">
        <Clock3 size={14} />
        Planned · Not implemented
      </span>
      <h2>Teaching demo video analysis</h2>
      <p className="candidate-teaching-intro">
        Analyze a recorded teaching demo for {job.name} across lesson content, body language,
        attitude and facial expression. This stage is designed but not implemented yet.
      </p>

      <div className="candidate-teaching-dimensions">
        {dimensions.map(dimension => (
          <article key={dimension.title}>
            <span className="landing-module-icon"><Presentation size={18} /></span>
            <div>
              <h3>{dimension.title}</h3>
              <p>{dimension.detail}</p>
            </div>
          </article>
        ))}
      </div>

      <button className="button secondary" onClick={onBack}>
        <ArrowLeft size={16} />
        Back to dossier
      </button>
    </div>
  );
}
