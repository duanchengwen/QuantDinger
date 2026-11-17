import test from 'node:test'
import assert from 'node:assert/strict'
import { quoteIsFresh, shanghaiDate, ruleIsReady, roleIsViewer } from '../src/views/ashare-paper/state.mjs'

const now = Date.parse('2026-11-17T02:30:20Z')
test('receipt freshness cannot conceal old upstream quote', () => {
  assert.equal(quoteIsFresh({ exchange_time: '2026-11-17T02:30:04Z', received_at: '2026-11-17T02:30:20Z' }, now), false)
  assert.equal(quoteIsFresh({ exchange_time: '2026-11-17T02:30:05Z', received_at: '2026-11-17T02:30:20Z' }, now), true)
})
test('browser clock invalidates a previously fresh row without another poll', () => {
  const quote = { exchange_time: '2026-11-17T02:30:20Z', received_at: '2026-11-17T02:30:20Z' }
  assert.equal(quoteIsFresh(quote, now), true)
  assert.equal(quoteIsFresh(quote, now + 16000), false)
})
test('missing, invalid and future timestamps never appear fresh', () => {
  for (const value of [null, { exchange_time: 'bad', received_at: 'bad' }, { exchange_time: '2026-11-17T02:30:21Z', received_at: '2026-11-17T02:30:21Z' }]) {
    assert.equal(quoteIsFresh(value, now), false)
  }
})
test('review uses the Shanghai date rather than host local date', () => {
  assert.equal(shanghaiDate(Date.parse('2026-11-17T15:59:59Z')), '2026-11-17')
  assert.equal(shanghaiDate(Date.parse('2026-11-17T16:00:00Z')), '2026-11-18')
})
test('review expires and unknown or ineligible statuses stay closed', () => {
  const rule = { reviewed: true, is_trading_day: true, trading_day: '2026-11-17', suspended: false, st: false }
  assert.equal(ruleIsReady(rule, now), true)
  assert.equal(ruleIsReady(rule, Date.parse('2026-11-17T16:00:00Z')), false)
  for (const change of [{ reviewed: false }, { is_trading_day: false }, { suspended: true }, { st: true }, { st: undefined }]) {
    assert.equal(ruleIsReady({ ...rule, ...change }, now), false)
  }
})
test('viewer role supports both official object and legacy string representation', () => {
  assert.equal(roleIsViewer('viewer'), true)
  assert.equal(roleIsViewer({ id: 'viewer' }), true)
  assert.equal(roleIsViewer({ id: 'user' }), false)
  assert.equal(roleIsViewer('admin'), false)
  assert.equal(roleIsViewer(null), false)
})
