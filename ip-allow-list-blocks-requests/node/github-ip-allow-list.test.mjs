import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ALLOW_LIST_QUERY, addressInMessage, allowListFromGraphql, cidrContains,
  classifyRefusal, coveredBy, egressAssumption, ipv4ToInt, listThatApplies,
  looksLikeIpv4, looksLikeIpv6, pairedReading, readCost, refusesMutation,
  repair, tokenKind, verdict,
} from './github-ip-allow-list.mjs';

const ALLOW_LIST_BODY = 'Although you appear to have the correct authorization '
  + 'credentials, the ACME organization has an IP allow list enabled, and '
  + '203.0.113.9 is not permitted to access this resource.';
const UA_BODY = 'Request forbidden by administrative rules. Please make sure '
  + 'your request has a User-Agent header.';

const ENTRIES = [
  { value: '198.51.100.0/24', active: true, name: 'office' },
  { value: '203.0.113.0/24', active: false, name: 'old ci' },
  { value: '2001:db8::/32', active: true, name: 'ipv6 office' },
];

test('the allow list refusal is the only one naming an address', () => {
  const [state, detail] = classifyRefusal(403, ALLOW_LIST_BODY, {});
  assert.equal(state, 'ip-allow-list');
  assert.match(detail, /names an IP address/);
  assert.equal(classifyRefusal(403, UA_BODY, {})[0], 'user-agent-rule');
  assert.equal(
    classifyRefusal(403, 'Resource not accessible by integration', {})[0],
    'permission-or-role',
  );
});

test('quota and secondary limits are sorted out first', () => {
  assert.equal(
    classifyRefusal(403, 'API rate limit exceeded for user ID 1.', {})[0],
    'primary-quota-exhausted',
  );
  assert.equal(
    classifyRefusal(429, 'You have exceeded a secondary rate limit.', {})[0],
    'secondary-limit',
  );
  assert.equal(
    classifyRefusal(403, '', { 'X-RateLimit-Remaining': '0' })[0],
    'primary-quota-exhausted',
  );
});

test('a reworded allow list message still classifies', () => {
  const reworded = 'Access from 198.51.100.77 is blocked by policy for this org.';
  assert.equal(classifyRefusal(403, reworded, {})[0], 'ip-allow-list');
});

test('an allow list message with no address is kept apart', () => {
  assert.equal(
    classifyRefusal(403, 'This org has an IP allow list enabled.', {})[0],
    'ip-allow-list-unaddressed',
  );
});

test('the address survives the full stop at the end of the sentence', () => {
  assert.equal(addressInMessage(ALLOW_LIST_BODY), '203.0.113.9');
  assert.equal(addressInMessage('from (2001:db8::1) today'), '2001:db8::1');
  assert.equal(addressInMessage('no address at all here'), null);
  assert.equal(addressInMessage('version 1.2.3.400 shipped'), null);
});

test('what an address looks like', () => {
  assert.equal(looksLikeIpv4('203.0.113.9'), true);
  assert.equal(looksLikeIpv4('203.0.113.256'), false);
  assert.equal(looksLikeIpv6('2001:db8::1'), true);
  assert.equal(looksLikeIpv6('203.0.113.9'), false);
});

test('cidr arithmetic at the edges', () => {
  assert.equal(cidrContains('203.0.113.0/24', '203.0.113.9'), true);
  assert.equal(cidrContains('203.0.113.0/24', '203.0.114.9'), false);
  assert.equal(cidrContains('203.0.113.9', '203.0.113.9'), true);
  assert.equal(cidrContains('0.0.0.0/0', '8.8.8.8'), true);
  assert.equal(ipv4ToInt('0.0.0.1'), 1);
});

test('an unevaluated entry is null and not false', () => {
  assert.equal(cidrContains('2001:db8::/32', '203.0.113.9'), null);
  assert.equal(cidrContains('not-a-cidr', '203.0.113.9'), null);
  assert.equal(cidrContains('203.0.113.0/xx', '203.0.113.9'), null);
});

test('an entry that exists but is switched off is its own finding', () => {
  const [state, entry] = coveredBy(ENTRIES, '203.0.113.9');
  assert.equal(state, 'covered-but-inactive');
  assert.equal(entry.name, 'old ci');
  assert.equal(verdict('ip-allow-list', state, 'ENABLED')[0], 'entry-exists-but-is-off');
});

test('coverage reports the entries it could not evaluate', () => {
  assert.equal(coveredBy(ENTRIES, '192.0.2.5')[0], 'not-covered-some-unevaluated');
  assert.equal(coveredBy(ENTRIES, '198.51.100.4')[0], 'covered');
  assert.equal(coveredBy([], '198.51.100.4')[0], 'no-entries');
});

test('a wrong egress assumption is named before anybody files a ticket', () => {
  const [state, detail] = egressAssumption(['198.51.100.0/24'], '203.0.113.9');
  assert.equal(state, 'egress-assumption-wrong');
  assert.match(detail, /would not have helped/);
  assert.equal(egressAssumption(['203.0.113.0/24'], '203.0.113.9')[0], 'egress-as-expected');
  assert.equal(egressAssumption([], '203.0.113.9')[0], 'nothing-declared');
});

test('the pair of readings is the headline', () => {
  const [state, detail] = pairedReading(403, 200);
  assert.equal(state, 'network-path');
  assert.match(detail, /source address/);
  assert.equal(pairedReading(403, 403)[0], 'refused-everywhere');
  assert.equal(pairedReading(403, null)[0], 'single-reading');
  assert.equal(pairedReading(200, 200)[0], 'no-refusal');
});

test('an installation token and a user token are judged differently', () => {
  assert.equal(listThatApplies('App installation token')[0], 'org-list-plus-app-managed');
  const [which, detail] = listThatApplies('App user-to-server token');
  assert.equal(which, 'org-list-only');
  assert.match(detail, /background sync works/);
  assert.equal(tokenKind('ghs_x'), 'App installation token');
  assert.equal(tokenKind('ghu_x'), 'App user-to-server token');
});

test('an unreadable list is not an empty one', () => {
  const [setting, , entries, note] = allowListFromGraphql({
    data: { organization: null },
    errors: [{ message: 'Resource not accessible' }],
  });
  assert.equal(setting, null);
  assert.deepEqual(entries, []);
  assert.match(note, /admin:org/);
  assert.equal(verdict('ip-allow-list', 'rule-unread', null)[0], 'rule-unreadable');
});

test('the entries are normalised off the graphql shape', () => {
  const [setting, apps, entries] = allowListFromGraphql({
    data: {
      organization: {
        ipAllowListEnabledSetting: 'ENABLED',
        ipAllowListForInstalledAppsEnabledSetting: 'DISABLED',
        ipAllowListEntries: {
          nodes: [{ allowListValue: '198.51.100.0/24', isActive: true, name: 'office' }],
        },
      },
    },
  });
  assert.equal(setting, 'ENABLED');
  assert.equal(apps, 'DISABLED');
  assert.deepEqual(entries, [{ value: '198.51.100.0/24', active: true, name: 'office' }]);
});

test('the query this script sends is a read', () => {
  assert.equal(refusesMutation(ALLOW_LIST_QUERY), false);
  assert.equal(refusesMutation('mutation M { createIpAllowListEntry { id } }'), true);
  assert.equal(refusesMutation('subscription S { x }'), true);
});

test('the repair asks a human and adds nothing', () => {
  const fix = repair('address-not-covered', '203.0.113.9', 'acme');
  assert.match(fix, /ask an owner of acme/);
  assert.match(fix, /203\.0\.113\.9\/32/);
  assert.match(fix, /adds anything/);
});

test('the two budgets are counted separately', () => {
  assert.deepEqual(readCost(), [2, 0]);
  assert.deepEqual(readCost(true), [2, 1]);
});
