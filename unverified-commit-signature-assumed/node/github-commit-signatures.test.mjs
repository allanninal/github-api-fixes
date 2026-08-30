import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  FAMILIES, REASONS, VIOLATIONS, authorAllowlistPass, disagreements,
  enforcementFromRules, familyOf, grade, identitySplit, readCost, repair,
  signaturePass, tally, verificationOf,
} from './github-commit-signatures.mjs';

function commit(sha, reason = null, verified = null, opts = {}) {
  const author = opts.author || 'alice@example.com';
  const inner = {
    author: { email: author, name: 'Alice' },
    committer: { email: opts.committer || author, name: 'Alice' },
  };
  if (opts.present !== false) {
    inner.verification = {
      verified, reason, signature: opts.signature ?? '-----BEGIN-----',
      payload: 'tree 1', verified_at: '2026-01-01T00:00:00Z',
    };
  }
  const linked = opts.linked === false ? null : { login: 'alice' };
  return { sha, commit: inner, author: linked, committer: linked };
}

const SIGNED = commit('aaa', 'valid', true);
const UNSIGNED = commit('bbb', 'unsigned', false, { signature: null });
const BAD = commit('ccc', 'invalid', false);
const UNREGISTERED = commit('ddd', 'unknown_key', false);
const OUTAGE = commit('eee', 'gpgverify_unavailable', false);
const ABSENT = commit('fff', null, null, { present: false });

test('every documented reason has a family and a sentence', () => {
  for (const [reason, [family, detail]] of Object.entries(REASONS)) {
    assert.ok(FAMILIES.includes(family), reason);
    assert.ok(detail.endsWith('.'), reason);
  }
  assert.equal(REASONS.valid[0], 'verified');
});

test('the four kinds of false are four different findings', () => {
  assert.equal(familyOf(verificationOf(UNSIGNED))[0], 'unsigned');
  assert.equal(familyOf(verificationOf(BAD))[0], 'signature-rejected');
  assert.equal(familyOf(verificationOf(UNREGISTERED))[0], 'identity-not-linked');
  assert.equal(familyOf(verificationOf(OUTAGE))[0], 'github-could-not-check');
  assert.match(familyOf(verificationOf(UNREGISTERED))[1], /cryptography is fine/);
});

test('a missing verification object is unknown and not false', () => {
  const normalised = verificationOf(ABSENT);
  assert.equal(normalised.present, false);
  assert.equal(normalised.verified, null);
  const [family, detail] = familyOf(normalised);
  assert.equal(family, 'verification-absent');
  assert.match(detail, /not unsigned/);
  assert.equal(signaturePass(ABSENT), null);
});

test('an outage is unknown rather than a violation', () => {
  assert.equal(signaturePass(OUTAGE), null);
  assert.ok(!VIOLATIONS.includes('github-could-not-check'));
  const [state, detail] = grade(tally([SIGNED, OUTAGE]), 'no-rule');
  assert.equal(state, 'checker-unavailable');
  assert.match(detail, /not a violation/);
});

test('a reason GitHub adds later is reported not defaulted', () => {
  const future = commit('ggg', 'quantum_key_rotated', false);
  const [family, detail] = familyOf(verificationOf(future));
  assert.equal(family, 'unknown-reason');
  assert.match(detail, /rather than letting it fall into a default/);
});

test('verified true beside the wrong reason is not believed', () => {
  assert.equal(familyOf(verificationOf(commit('hhh', 'unsigned', true)))[0], 'unknown-reason');
  assert.equal(familyOf(verificationOf(commit('iii', 'valid', false)))[0], 'unknown-reason');
});

test('the author check and the signature check disagree in both directions', () => {
  const allowed = ['alice@example.com'];
  assert.equal(disagreements([UNSIGNED], allowed)[0].gap, 'author-passed-signature-did-not');
  const outsider = commit('jjj', 'valid', true, { author: 'carol@example.com' });
  assert.equal(disagreements([outsider], allowed)[0].gap, 'signature-passed-author-did-not');
  assert.deepEqual(disagreements([SIGNED], allowed), []);
});

test('the author check authenticates nothing', () => {
  const forged = commit('kkk', 'unsigned', false, { signature: null });
  assert.equal(authorAllowlistPass(forged, ['alice@example.com']), true);
  assert.equal(signaturePass(forged), false);
  assert.equal(authorAllowlistPass(forged, []), null);
});

test('the signature speaks for the committer not the author', () => {
  const split = commit('lll', 'valid', true, { committer: 'bob@example.com' });
  const [state, detail] = identitySplit(split);
  assert.equal(state, 'author-differs-from-committer');
  assert.match(detail, /speaks for the committer/);
  assert.equal(identitySplit(SIGNED)[0], 'author-is-committer');
  const unlinked = commit('mmm', 'valid', true, { linked: false });
  assert.equal(identitySplit(unlinked)[0], 'email-resolves-to-no-account');
});

test('an unreadable rule is not an absent rule', () => {
  const [state, detail] = enforcementFromRules(null, false);
  assert.equal(state, 'rule-unreadable');
  assert.match(detail, /not the same as unenforced/);
  assert.equal(enforcementFromRules([], true)[0], 'no-rule');
  assert.equal(
    enforcementFromRules([{ type: 'deletion' }, { type: 'required_signatures' }], true)[0],
    'enforced',
  );
});

test('a verified history with no rule is not a guarantee', () => {
  const counts = tally([SIGNED, SIGNED]);
  assert.equal(grade(counts, 'no-rule')[0], 'verified-but-not-enforced');
  assert.equal(grade(counts, 'enforced')[0], 'verified-and-enforced');
  assert.match(grade(counts, 'no-rule')[1], /not a constraint/);
});

test('absent verification outranks every other grade', () => {
  assert.equal(grade(tally([UNSIGNED, ABSENT]), 'enforced')[0], 'verification-unknown');
});

test('the tally covers every family', () => {
  const counts = tally([SIGNED, UNSIGNED, BAD, UNREGISTERED, OUTAGE, ABSENT]);
  for (const name of FAMILIES) assert.ok(name in counts);
  assert.equal(counts.verified, 1);
  assert.equal(counts['verification-absent'], 1);
});

test('the repair asks a human and writes nothing', () => {
  const fix = repair('identity-not-linked-present', 'no-rule', 'acme/payments', 'main');
  assert.match(fix, /add their public keys/);
  assert.match(fix, /ask an admin of acme\/payments/);
  assert.ok(fix.endsWith('Nothing here writes.'));
  assert.match(
    repair('verified-and-enforced', 'rule-unreadable', 'acme/payments', 'main'),
    /unreadable is not unenforced/,
  );
});

test('the read cost is known before anything is spent', () => {
  assert.equal(readCost(1, false), 1);
  assert.equal(readCost(3, true), 4);
  assert.equal(readCost(0, false), 1);
});
