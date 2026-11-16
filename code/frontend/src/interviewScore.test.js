import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreCriteria, scoreInterview } from './interviewScore.js';

const criteria = (...statuses) => statuses.map((status, index) => ({ index, status }));

test('covered earns one point, partial half; no answer is a real zero', () => {
  assert.deepEqual(scoreCriteria(criteria('met', 'partial', 'not_addressed')), {
    points: 1.5, total: 3, score: 50, counts: { met: 1, partial: 1, not_addressed: 1 },
  });
  assert.equal(scoreCriteria(criteria('not_addressed')).score, 0);
  assert.equal(scoreCriteria(criteria('met', 'met')).score, 100);
});

test('overall score weights criteria equally, not questions with different criterion counts', () => {
  const result = scoreInterview([{ criteria: criteria('met') }, { criteria: criteria(...Array(9).fill('not_addressed')) }]);
  assert.equal(result.score, 10);
  assert.equal(result.points, 1);
  assert.equal(result.total, 10);
  assert.deepEqual(result.counts, { found: 1, partial: 0, not_found: 1 });
});

test('overall score is calculated before rounding individual scores', () => {
  const result = scoreInterview([{ criteria: criteria('met', 'not_addressed', 'not_addressed') }, { criteria: criteria('partial') }]);
  assert.deepEqual(result.questions.map(question => question.score), [33, 50]);
  assert.equal(result.score, 38);
  assert.deepEqual(result.counts, { found: 0, partial: 2, not_found: 0 });
});

test('missing or invalid criteria cannot silently become a score or inflate the aggregate', () => {
  for (const value of [null, [], [{ status: 'unknown' }], [null]]) assert.equal(scoreCriteria(value), null);
  for (const value of [null, [], [null], [{ criteria: criteria('met') }, { criteria: [] }]]) assert.equal(scoreInterview(value), null);
});
