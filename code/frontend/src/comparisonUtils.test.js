import test from 'node:test';
import assert from 'node:assert/strict';
import { fraction, heatColor, sortRows, incomplete, sectionColor } from './comparisonUtils.js';

test('heatmap compares fractions, not raw points; missing values are not zero', () => {
  assert.equal(heatColor(4, 5), heatColor(24, 30));
  assert.equal(fraction(null, 30), null);
  assert.equal(heatColor(undefined, 30), null);
  assert.notEqual(heatColor(0, 30), null);
  assert.equal(fraction(10, 0), null);
});
test('sorting keeps missing scores last in either direction and keeps real zeroes', () => {
  const rows = [{ name: 'Missing', total: null, sections: {} }, { name: 'Zero', total: 0, sections: { education: 0 } }, { name: 'High', total: 70, sections: { education: 15 } }];
  assert.deepEqual(sortRows(rows, { column: 'total', direction: 1 }).map(r => r.name), ['Zero', 'High', 'Missing']);
  assert.deepEqual(sortRows(rows, { column: 'education', direction: -1 }).map(r => r.name), ['High', 'Zero', 'Missing']);
  assert.equal(rows[0].name, 'Missing');
});
test('incomplete evidence is based on saved status, not score size', () => {
  const row = { assessment: { sections: [{ section_id: 'education', score: 5, status: 'insufficient_evidence' }, { section_id: 'research', score: 0, status: 'supported' }] } };
  assert.equal(incomplete(row, 'education'), true);
  assert.equal(incomplete(row, 'research'), false);
  assert.equal(incomplete({}, 'research'), false);
});
test('education keeps its color across template section IDs', () => {
  assert.equal(sectionColor({id:'education',title:'Education'}), sectionColor({id:'academic_education',title:'Education and qualifications'}));
  assert.notEqual(sectionColor({id:'education',title:'Education'}), sectionColor({id:'publications',title:'Publications'}));
});
