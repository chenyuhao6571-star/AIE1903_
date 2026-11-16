export const criterionLabels = { met: 'Covered', partial: 'Partial', not_addressed: 'Not answered' };
const weights = { met: 1, partial: 0.5, not_addressed: 0 };

export function scoreCriteria(criteria) {
  if (!Array.isArray(criteria) || !criteria.length || criteria.some(item => !Object.hasOwn(weights, item?.status))) return null;
  const counts = { met: 0, partial: 0, not_addressed: 0 };
  let points = 0;
  for (const criterion of criteria) {
    counts[criterion.status] += 1;
    points += weights[criterion.status];
  }
  return { points, total: criteria.length, score: Math.round(100 * points / criteria.length), counts };
}

export function scoreInterview(results) {
  if (!Array.isArray(results) || !results.length) return null;
  const questions = results.map(result => scoreCriteria(result?.criteria));
  if (questions.some(question => !question)) return null;
  const total = questions.reduce((sum, question) => sum + question.total, 0);
  const points = questions.reduce((sum, question) => sum + question.points, 0);
  const counts = { found: 0, partial: 0, not_found: 0 };
  for (const question of questions) {
    counts[question.points === question.total ? 'found' : question.points > 0 ? 'partial' : 'not_found'] += 1;
  }
  return { score: Math.round(100 * points / total), points, total, counts, questions };
}
