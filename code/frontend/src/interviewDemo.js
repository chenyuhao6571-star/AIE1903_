// Illustrative coverage and answers only. No model calls or candidate scoring.
export const FACTORS = [
  { id: 'teaching', title: 'Teaching', coverage: 10, focus: 'Teaching responsibility and outcomes', section: 'teaching' },
  { id: 'awards', title: 'Awards', coverage: 25, focus: 'Recognition and selection criteria', section: 'achievements_awards' },
  { id: 'service', title: 'Academic service', coverage: 40, focus: 'Contribution beyond research', section: 'work_experience' },
  { id: 'research', title: 'Research', coverage: 80, focus: 'Independent contribution and next steps', section: 'research' },
  { id: 'publications', title: 'Publications', coverage: 90, focus: 'Personal contribution and impact', section: 'publications' },
];

export const QUESTION_DETAILS = {
  teaching: {
    clarification: 'Teaching responsibilities, workload and student outcomes need clarification.',
    criteria: ['Teaching role: full-time, part-time or teaching assistant.', 'Courses, student level, class size and teaching load.', 'Personal responsibility for course design and assessment.', 'Concrete evidence of student learning.'],
  },
  awards: {
    clarification: 'Award selection criteria and the candidate’s personal contribution need clarification.',
    criteria: ['Award name, year and awarding organization.', 'Selection criteria and scope of the award.', 'Individual contribution to the recognized work.'],
  },
  service: {
    clarification: 'The scale and outcomes of academic service need clarification.',
    criteria: ['Specific mentoring, committee or community activity.', 'Personal responsibility and duration of involvement.', 'Who benefited and what changed as a result.'],
  },
  research: {
    clarification: 'Independent research responsibility and plans for this position need clarification.',
    criteria: ['Research question and chosen method.', 'Personal contribution versus the team’s contribution.', 'Results, limitations and evidence.', 'Feasible next steps relevant to the position.'],
  },
  publications: {
    clarification: 'The candidate’s contribution and the impact of a publication need clarification.',
    criteria: ['Identification of a specific publication.', 'Personal role in the research and writing.', 'Main finding and evidence of its impact.'],
  },
};

export function questionDetails(question) {
  const defaults = QUESTION_DETAILS[question.factor];
  return {
    ...question,
    // Remove the old generated introduction without losing reviewer edits.
    text: question.clarification ? question.text : question.text.replace(/^Your profile lists “.+?”\.\s*/s, ''),
    clarification: question.clarification || defaults.clarification,
    criteria: Array.isArray(question.criteria) && question.criteria.length ? question.criteria : [...defaults.criteria],
  };
}

export function makeDemoQuestions(job) {
  return FACTORS.map(factor => {
    const record = job.profile?.[factor.section]?.[0];
    const prompts = {
      teaching: 'Which courses have you taught, in what role and with what workload? Describe how you assessed student learning.',
      awards: 'What were your awards given for? Explain the selection criteria and your personal contribution to the recognized work.',
      service: 'How have you contributed to your university through mentoring, committees or academic service? Describe your role and the outcome.',
      research: 'Describe a research project you led independently. What was your contribution, and how would you develop it in this position?',
      publications: 'Choose one publication and explain your personal contribution, the main result and its impact.',
    };
    return {
      id: crypto.randomUUID(), factor: factor.id, text: prompts[factor.id], approved: false,
      clarification: QUESTION_DETAILS[factor.id].clarification,
      criteria: [...QUESTION_DETAILS[factor.id].criteria],
      evidence: record ? [record.title || record.role, record.description || record.organization || record.venue].filter(Boolean).join(' — ') : '',
      sources: record?.source_pages || [],
    };
  });
}

export const DEMO_ANSWERS = {
  teaching: {
    answer: 'I supported an undergraduate course, led practical sessions, and helped students with their projects. I adapted explanations when students struggled with the material.',
    assessment: 'Needs follow-up',
    strength: 'The example describes direct contact with students and an approach to explaining difficult material.',
    gap: 'Clarify whether the role was full-time, the class size, and evidence of student learning.',
  },
  awards: {
    answer: 'Our team received recognition for a research project. I contributed to the experiments and helped prepare the work for presentation.',
    assessment: 'Needs detail',
    strength: 'The example identifies a contribution to a team achievement.',
    gap: 'Ask for the award name, year, awarding body, and selection criteria.',
  },
  service: {
    answer: 'I helped organize a departmental seminar and mentored junior students. I coordinated speakers and supported students as they prepared their first presentations.',
    assessment: 'Partially supported',
    strength: 'The example gives concrete activities beyond individual research.',
    gap: 'Clarify the duration, scale, and outcomes of the contribution.',
  },
  research: {
    answer: 'I developed the research question, designed the experiments, and coordinated the project. My next step would be to evaluate the method on a broader range of real-world conditions.',
    assessment: 'Clear example',
    strength: 'The example distinguishes personal responsibility and proposes a next step.',
    gap: 'Request the main result, a limitation, and a link to the position’s research priorities.',
  },
  publications: {
    answer: 'For one of our papers, I designed the evaluation and led the analysis. The findings informed our next study and helped us understand where the method works well.',
    assessment: 'Partially supported',
    strength: 'The example explains a specific role in producing a publication.',
    gap: 'Ask for the publication title, a measurable result, and evidence of impact.',
  },
};

export function timeLabel(seconds) {
  const value = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`;
}
