import test from 'node:test'
import assert from 'node:assert/strict'
import { createThemeState, createThemeBrowser, themeMembers, themeHasNext, themePageRange } from '../src/views/ashare-paper/theme-state.mjs'

const catalog = { status: 'observed', themes: [{ board_code: 'BK0800', board_name: '人工智能' }, { board_code: 'BK0900', board_name: 'fixture' }] }
const snapshot = (board = 'BK0800', count = 1, members = [{ symbol: 'sz.000001', name: 'fixture' }]) => ({ status: 'observed', board_code: board, member_count: count, members })
const flush = async () => { await Promise.resolve(); await Promise.resolve() }

function harness () {
  const state = createThemeState()
  const pending = []
  const request = path => new Promise((resolve, reject) => { pending.push({ path, resolve, reject }) })
  return { state, pending, browser: createThemeBrowser(state, request, () => 'localized failure') }
}

async function loaded (view, data = snapshot()) {
  const promise = view.browser.ensureLoaded()
  view.pending[0].resolve(catalog)
  await flush()
  view.pending[1].resolve(data)
  await promise
}

test('theme data loads only once on first entry and again on explicit refresh', async () => {
  const view = harness()
  assert.equal(view.pending.length, 0)
  await loaded(view)
  assert.deepEqual(view.pending.map(item => item.path), ['/themes', '/themes/BK0800?limit=100&offset=0'])
  await view.browser.ensureLoaded()
  assert.equal(view.pending.length, 2)
  const refresh = view.browser.refresh()
  assert.equal(view.state.snapshot, null)
  assert.equal(view.state.snapshotError, '')
  view.pending[2].resolve(catalog)
  await flush()
  view.pending[3].resolve(snapshot())
  await refresh
  assert.equal(view.pending.length, 4)
})

test('a late response for an old selection cannot overwrite the new board', async () => {
  const view = harness()
  const first = view.browser.ensureLoaded()
  view.pending[0].resolve(catalog)
  await flush()
  assert.equal(view.state.catalogLoading, false)
  const selected = view.browser.select('BK0900')
  assert.equal(view.state.snapshot, null)
  view.pending[2].resolve(snapshot('BK0900'))
  await selected
  view.pending[1].resolve(snapshot('BK0800'))
  await first
  assert.equal(view.state.snapshot.board_code, 'BK0900')
  assert.equal(view.state.snapshotLoading, false)
})

test('late failures and finally blocks cannot clear a newer loading state', async () => {
  const view = harness()
  const first = view.browser.ensureLoaded()
  view.pending[0].resolve(catalog)
  await flush()
  const selected = view.browser.select('BK0900')
  view.pending[1].reject(new Error('old failure'))
  await first
  assert.equal(view.state.snapshotError, '')
  assert.equal(view.state.snapshotLoading, true)
  view.pending[2].resolve(snapshot('BK0900'))
  await selected
  assert.equal(view.state.snapshotLoading, false)
})

test('refresh clears old results and a failed latest request shows a local error', async () => {
  const view = harness()
  await loaded(view)
  const refresh = view.browser.refresh()
  assert.equal(view.state.snapshot, null)
  assert.deepEqual(view.state.themes, [])
  view.pending[2].reject(new Error('server details never shown'))
  await refresh
  assert.equal(view.state.catalogError, 'localized failure')
  assert.equal(view.state.selectedBoard, '')
  assert.equal(view.state.catalogLoading, false)
  assert.equal(view.state.snapshotLoading, false)
})

test('not observed is distinct from a recorded pool with zero supported members', async () => {
  const missing = harness()
  const promise = missing.browser.ensureLoaded()
  missing.pending[0].resolve({ status: 'not_observed', themes: [] })
  await promise
  assert.equal(missing.state.catalogStatus, 'not_observed')
  assert.equal(missing.state.snapshot, null)
  assert.equal(missing.pending.length, 1)
  const empty = harness()
  await loaded(empty, snapshot('BK0800', 0, []))
  assert.equal(empty.state.snapshot.status, 'observed')
  assert.deepEqual(themePageRange(empty.state), { start: 0, end: 0, total: 0 })
  assert.equal(themeHasNext(empty.state), false)
  assert.deepEqual(themeMembers({ snapshot: { status: 'not_observed', members: [{ symbol: 'must-not-show' }] } }), [])
})

test('pagination uses archived member count instead of returned row count', async () => {
  const view = harness()
  await loaded(view, snapshot('BK0800', 250))
  assert.equal(themeMembers(view.state).length, 1)
  assert.equal(themeHasNext(view.state), true)
  const next = view.browser.page(1)
  assert.equal(view.pending[2].path, '/themes/BK0800?limit=100&offset=100')
  view.pending[2].resolve(snapshot('BK0800', 250))
  await next
  assert.deepEqual(themePageRange(view.state), { start: 101, end: 101, total: 250 })
  const last = view.browser.page(1)
  view.pending[3].resolve(snapshot('BK0800', 250))
  await last
  assert.equal(view.state.offset, 200)
  assert.equal(themeHasNext(view.state), false)
  await view.browser.page(1)
  assert.equal(view.pending.length, 4)
  const back = view.browser.page(-1)
  view.pending[4].resolve(snapshot('BK0800', 250))
  await back
  assert.equal(view.state.offset, 100)
  const selected = view.browser.select('BK0900')
  assert.equal(view.state.offset, 0)
  view.pending[5].resolve(snapshot('BK0900'))
  await selected
})

test('an older catalog response cannot undo a refreshed catalog selection', async () => {
  const view = harness()
  const first = view.browser.ensureLoaded()
  const latest = view.browser.refresh()
  view.pending[1].resolve({ status: 'observed', themes: [catalog.themes[1]] })
  await flush()
  view.pending[2].resolve(snapshot('BK0900'))
  await latest
  view.pending[0].resolve(catalog)
  await first
  assert.equal(view.state.selectedBoard, 'BK0900')
  assert.equal(view.state.themes.length, 1)
  assert.equal(view.state.snapshot.board_code, 'BK0900')
})

test('only archived choices are requested and mismatched response boards fail locally', async () => {
  const view = harness()
  await loaded(view)
  await view.browser.select('unarchived')
  assert.equal(view.pending.length, 2)
  assert.equal(view.state.snapshot, null)
  const selected = view.browser.select('BK0900')
  view.pending[2].resolve(snapshot('BK0800'))
  await selected
  assert.equal(view.state.snapshot, null)
  assert.equal(view.state.snapshotError, 'localized failure')
})

test('destroyed views ignore pending responses and never request again', async () => {
  const view = harness()
  const first = view.browser.ensureLoaded()
  view.browser.dispose()
  view.pending[0].resolve(catalog)
  await first
  assert.equal(view.pending.length, 1)
  assert.equal(view.state.snapshot, null)
  await view.browser.refresh()
  await view.browser.select('BK0800')
  assert.equal(view.pending.length, 1)
})
