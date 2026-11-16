import QuestionsView from './QuestionsView';
import VideoInterviewView from './VideoInterviewView';
import './interview.css';

export default function InterviewWorkspace({ job, view, onSource, onNavigate }) {
  return <div className="interview-workspace">
    {view === 'questions' ? (
      <QuestionsView job={job} onSource={onSource} onNavigate={onNavigate}/>
    ) : (
      <VideoInterviewView
        job={job}
        initialPanel={view === 'video-clips' ? 'transcript' : view === 'video-answers' ? 'analysis' : null}
        onQuestions={() => onNavigate('questions')}
      />
    )}
  </div>;
}
