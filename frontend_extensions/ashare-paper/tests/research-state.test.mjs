import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createResearchState, createResearchBrowser, researchCanGenerate, researchCitations, safeNewsUrl } from '../src/views/ashare-paper/research-state.mjs'

const status = { model_configured: true, blocked_reason: null, execution_allowed: false }
const run = (id = 'r1', state = 'completed') => ({ id, status: state, candidates: [], risks: [], execution_allowed: false, approval_status: 'not_submitted' })
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve() }
function harness (maxPolls = 24) {
  const state = createResearchState()
  const pending = []
  const timers = new Map()
  let nextTimer = 0
  let nextKey = 0
  const request = (path, method, data) => new Promise((resolve, reject) => pending.push({ path, method, data, resolve, reject }))
  const controller = createResearchBrowser(state, request, () => 'localized failure', {
    schedule: callback => { const id = ++nextTimer; timers.set(id, callback); return id },
    cancel: id => timers.delete(id), makeKey: () => 'key-' + ++nextKey, maxPolls
  })
  return { state, pending, timers, controller }
}
async function resolveList (view, offset, records = [], config = status, news = []) {
  view.pending[offset].resolve(config)
  view.pending[offset + 1].resolve({ items: news })
  view.pending[offset + 2].resolve({ items: records })
  await flush()
}
async function load (view, records = [], config = status) {
  const promise = view.controller.setActive(true)
  await resolveList(view, 0, records, config)
  if (records.length) view.pending[3].resolve(records[0])
  await promise
}

test('research reads only on active tab and never creates a run implicitly', async () => {
  const view = harness()
  await view.controller.refresh()
  await view.controller.generate()
  assert.equal(view.pending.length, 0)
  await load(view)
  assert.deepEqual(view.pending.map(r => r.path), ['/research/status', '/news?limit=20', '/research/runs?limit=10'])
  assert.deepEqual(view.state.news, [])
  assert.deepEqual(view.state.runs, [])
  assert.equal(view.state.detail, null)
  assert.equal(view.timers.size, 0)
  assert.equal(researchCanGenerate(view.state), true)
})

test('missing model configuration and unsupported models disable generation', async () => {
  for (const config of [{ model_configured: false, blocked_reason: 'needs_model_configuration' }, { model_configured: true, blocked_reason: 'model_not_supported' }]) {
    const view = harness()
    await load(view, [], config)
    await view.controller.generate()
    assert.equal(view.pending.length, 3)
    assert.equal(researchCanGenerate(view.state), false)
  }
})

test('failed refresh clears old evidence and reports only a localized error', async () => {
  const view = harness()
  await load(view, [run()])
  const promise = view.controller.refresh()
  assert.deepEqual(view.state.news, [])
  assert.deepEqual(view.state.runs, [])
  assert.equal(view.state.detail, null)
  view.pending[4].reject(new Error('server secret'))
  view.pending[5].resolve({ items: [] })
  view.pending[6].resolve({ items: [] })
  await promise
  assert.equal(view.state.error, 'localized failure')
  assert.equal(view.state.status, null)
  assert.equal(view.state.loading, false)
  assert.equal(researchCanGenerate(view.state), false)
})

test('out of order refreshes and failures cannot overwrite current data or loading', async () => {
  const view = harness()
  const old = view.controller.setActive(true)
  const latest = view.controller.refresh()
  await resolveList(view, 3)
  await latest
  view.pending[0].reject(new Error('late error'))
  view.pending[1].resolve({ items: [{ id: 'stale' }] })
  view.pending[2].resolve({ items: [run('stale')] })
  await old
  assert.deepEqual(view.state.news, [])
  assert.equal(view.state.error, '')
  assert.equal(view.state.loading, false)
})

test('selection changes suppress late run details and late errors', async () => {
  const view = harness()
  const first = view.controller.setActive(true)
  await resolveList(view, 0, [run('r1'), run('r2')])
  const selected = view.controller.select('r2')
  assert.equal(view.state.detail, null)
  view.pending[3].reject(new Error('late secret'))
  await first
  assert.equal(view.state.detailLoading, true)
  assert.equal(view.state.detailError, '')
  view.pending[4].resolve(run('r2'))
  await selected
  assert.equal(view.state.detail.id, 'r2')
  const mismatched = view.controller.select('r1')
  view.pending[5].resolve(run('r2'))
  await mismatched
  assert.equal(view.state.detail, null)
  assert.equal(view.state.detailError, 'localized failure')
})

test('empty completed research stays empty and failed runs cannot be shown as completed', async () => {
  const view = harness()
  await load(view, [run()])
  assert.deepEqual(view.state.detail.candidates, [])
  const refresh = view.controller.refresh()
  await resolveList(view, 4, [run('r2', 'no_evidence')])
  view.pending[7].resolve(run('r2', 'no_evidence'))
  await refresh
  assert.equal(view.state.detail.status, 'no_evidence')
  assert.deepEqual(view.state.detail.candidates, [])
  assert.equal(view.timers.size, 0)
})

test('explicit submissions deduplicate concurrent clicks and retry the same key after uncertainty', async () => {
  const view = harness()
  await load(view)
  const first = view.controller.generate()
  await view.controller.generate()
  assert.equal(view.pending.length, 4)
  assert.equal(view.pending[3].method, 'post')
  assert.deepEqual(view.pending[3].data, { idempotency_key: 'key-1' })
  view.pending[3].reject(new Error('network timeout'))
  await first
  assert.equal(view.state.submitError, 'localized failure')
  assert.equal(view.state.retryPending, true)
  const retry = view.controller.generate()
  assert.deepEqual(view.pending[4].data, { idempotency_key: 'key-1' })
  view.pending[4].resolve(run('r1', 'queued'))
  await flush()
  await resolveList(view, 5, [run('r1', 'queued')])
  view.pending[8].resolve(run('r1', 'queued'))
  await retry
  assert.equal(view.state.retryPending, false)
  assert.equal(view.state.submitError, '')
  assert.equal(researchCanGenerate(view.state), false)
  assert.equal(view.timers.size, 1)
})

test('polling is bounded, never submits, and stops once research completes', async () => {
  const view = harness(1)
  await load(view, [run('r1', 'started')])
  const [timer, callback] = [...view.timers][0]
  view.timers.delete(timer)
  const poll = callback()
  await resolveList(view, 4, [run('r1', 'started')])
  view.pending[7].resolve(run('r1', 'started'))
  await poll
  assert.equal(view.state.pollExhausted, true)
  assert.equal(view.timers.size, 0)
  assert.ok(view.pending.every(item => !item.method))
  const refresh = view.controller.refresh()
  await resolveList(view, 8, [run()])
  view.pending[11].resolve(run())
  await refresh
  assert.equal(view.state.pollExhausted, false)
  assert.equal(view.timers.size, 0)
})

test('leaving the tab cancels polling and suppresses pending reads', async () => {
  const view = harness()
  await load(view, [run('r1', 'queued')])
  view.controller.setActive(false)
  assert.equal(view.timers.size, 0)
  assert.equal(view.state.detail, null)
  await view.controller.refresh()
  await view.controller.select('r1')
  await view.controller.generate()
  assert.equal(view.pending.length, 4)
  const reopened = view.controller.setActive(true)
  view.controller.setActive(false)
  await resolveList(view, 4, [run('r2')])
  await reopened
  assert.equal(view.pending.length, 7)
  assert.equal(view.state.detail, null)
})

test('a submission completed after the tab closes causes no hidden follow-up request', async () => {
  const view = harness()
  await load(view)
  const submission = view.controller.generate()
  view.controller.setActive(false)
  view.pending[3].resolve(run('r1', 'queued'))
  await submission
  assert.equal(view.pending.length, 4)
  assert.equal(view.timers.size, 0)
  assert.equal(view.state.submitting, false)
})

test('disposed views ignore responses and cannot start further requests', async () => {
  const view = harness()
  const promise = view.controller.setActive(true)
  view.controller.dispose()
  await resolveList(view, 0, [run()])
  await promise
  await view.controller.setActive(true)
  await view.controller.refresh()
  assert.equal(view.pending.length, 3)
  assert.equal(view.state.detail, null)
})

test('news links allow only the public archive HTTPS hosts', () => {
  assert.equal(safeNewsUrl('https://www.cninfo.com.cn/new/disclosure/detail?id=1'), 'https://www.cninfo.com.cn/new/disclosure/detail?id=1')
  assert.equal(safeNewsUrl('https://static.cninfo.com.cn/finalpage/a.PDF'), 'https://static.cninfo.com.cn/finalpage/a.PDF')
  for (const value of ['javascript:alert(1)', 'data:text/html,test', 'http://www.cninfo.com.cn/a', 'https://www.cninfo.com.cn.evil.example/a', 'https://evil.example/a', 'https://user:password@www.cninfo.com.cn/a', null]) assert.equal(safeNewsUrl(value), '')
})

test('new research copy is localized in all official languages', async () => {
  const source = await readFile(new URL('../src/locales/ashare-paper.js', import.meta.url), 'utf8')
  const { default: locales } = await import('data:text/javascript;charset=utf-8,' + encodeURIComponent(source))
  const keys = Object.keys(locales['en-US']).filter(key => key.startsWith('ashare.research'))
  for (const language of ['en-US', 'zh-CN', 'zh-TW']) for (const key of keys) assert.equal(typeof locales[language][key], 'string', language + ': ' + key)
  assert.notEqual(locales['zh-CN']['ashare.researchTab'], locales['zh-TW']['ashare.researchTab'])
})

test('issuer announcement titles and no-evidence reasons keep their precise meaning', async () => {
  const { researchEvidenceKey, researchReasonKey } = await import('../src/views/ashare-paper/research-state.mjs')
  assert.equal(researchEvidenceKey('issuer_announcement_title'), 'researchIssuerTitle')
  assert.equal(researchEvidenceKey('issuer_announcement_excerpt'), 'researchIssuerExcerpt')
  assert.equal(researchEvidenceKey('theme_context'), 'researchThemeContext')
  assert.equal(researchEvidenceKey('unexpected'), 'researchEvidenceUnknown')
  assert.equal(researchReasonKey('no_supported_topics'), 'researchNoSupportedTopics')
  assert.equal(researchReasonKey('no_verified_theme_members'), 'researchNoThemeEvidence')
  assert.equal(researchReasonKey('no_news_evidence'), 'researchNoFreshIssuerEvidence')
  assert.equal(researchReasonKey('model_not_supported'), 'researchModelUnsupported')
  assert.equal(researchReasonKey('news_excerpt_source_mismatch'), 'researchEvidenceUnavailable')
  assert.equal(researchReasonKey('server secret must never appear'), 'researchReasonUnavailable')
})

test('candidate citations resolve the exact frozen announcement revision and issuer', () => {
  const old = { id: 'archive-a:1', source: 'cninfo', content_kind: 'announcement_title', title: 'Original revision',
                verified_issuer_symbols: ['sh.600000'], excerpt: { content_kind: 'announcement_pdf_excerpt', content: 'Frozen excerpt' } }
  const replacement = { ...old, id: 'archive-b:1', title: 'Later revision' }
  const wrongIssuer = { ...old, id: 'archive-a:2', verified_issuer_symbols: ['sz.000001'] }
  const candidate = { symbol: 'sh.600000', evidence_news_ids: [old.id, 'missing', wrongIssuer.id, old.id] }
  const result = { ...run(), input_snapshot: { news: [replacement, old, wrongIssuer] } }
  assert.deepEqual(researchCitations(result, candidate), [
    { id: old.id, article: old }, { id: 'missing', article: null }, { id: wrongIssuer.id, article: null }
  ])
  assert.deepEqual(researchCitations({ ...result, status: 'started' }, candidate), [])
  const duplicate = { ...result, input_snapshot: { news: [old, { ...replacement, id: old.id }] } }
  assert.deepEqual(researchCitations(duplicate, { ...candidate, evidence_news_ids: [old.id] }), [{ id: old.id, article: null }])
})
