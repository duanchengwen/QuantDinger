import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/views/ashare-paper/strategy-versions.vue', import.meta.url), 'utf8')
const script = source.split('<script>')[1].split('</script>')[0]
  .replace("import { paperRequest } from '@/api/asharePaper'", '')
  .replace('export default', 'return')

function mount (request, canWrite = true) {
  const component = new Function('paperRequest', 'crypto', script)(request, webcrypto)
  const state = {
    ...component.data(), canWrite, symbols: ['sz.000001'],
    $t: key => key,
    $confirm: dialog => { state.dialog = dialog }
  }
  for (const [name, method] of Object.entries(component.methods)) state[name] = method.bind(state)
  for (const [name, getter] of Object.entries(component.computed)) {
    Object.defineProperty(state, name, { get: getter.bind(state) })
  }
  state.applyResearchSeed = component.watch.researchSeed.handler.bind(state)
  state.selectionChanged = component.watch.selectedId.bind(state)
  return state
}

test('approve only after confirmation and bind to the displayed version and hash', async () => {
  const first = { id: 'a'.repeat(32), config_sha256: 'f'.repeat(64), status: 'draft' }
  const other = { id: 'b'.repeat(32), config_sha256: '0'.repeat(64), status: 'draft' }
  const calls = []
  const state = mount((...args) => {
    calls.push(args)
    return { ...first, status: 'approved', execution_allowed: false }
  })
  state.versions = [first, other]
  state.selectedId = first.id
  state.confirmDecision(state.selected, 'approved')
  assert.equal(calls.length, 0)
  assert.ok(state.dialog.content.includes(first.config_sha256))
  state.selectedId = other.id
  await state.dialog.onOk()
  assert.deepEqual(calls, [[`/strategy-versions/${first.id}/decision`, 'post', {
    config_sha256: first.config_sha256, decision: 'approved', confirmed: true
  }]])
  assert.equal(state.selected.execution_allowed, false)
})

test('a viewer cannot save or record a decision', async () => {
  const state = mount(() => assert.fail('Unexpected write request'), false)
  state.confirmDecision({ id: 'a'.repeat(32) }, 'approved')
  assert.equal(state.dialog, undefined)
  await state.create()
  await state.decide({}, 'approved')
})

test('activation uses a separate, explicit confirmation', async () => {
  const version = { id: 'a'.repeat(32), config_sha256: 'f'.repeat(64), status: 'approved' }
  const calls = []
  const state = mount((...args) => {
    calls.push(args)
    return { ...version, execution_allowed: true }
  })
  state.confirmDecision(version, 'activated')
  assert.equal(calls.length, 0)
  assert.ok(state.dialog.content.includes('ashare.strategyActivateConfirm'))
  await state.dialog.onOk()
  assert.equal(calls[0][2].decision, 'activated')
  assert.equal(state.versionStatus(state.selected), 'ashare.strategyStatus.activated')
})

test('retry an uncertain draft save with the same request key', async () => {
  const requests = []
  const state = mount((path, method, data) => {
    requests.push(data)
    if (requests.length === 1) throw new Error('timeout')
    return { id: 'a'.repeat(32), config: data.config, status: 'draft' }
  })
  Object.assign(state.draft, { symbol: 'sz.000001', limit_price: 10, threshold: 10, max_cash: 1010 })
  await state.create()
  await state.create()
  assert.equal(requests.length, 2)
  assert.deepEqual(requests[0], requests[1])
  assert.equal(state.selected.status, 'draft')
})

test('opening a candidate starts a blank draft and saving attaches only its research reference', async () => {
  const calls = []
  const state = mount((path, method, data) => {
    calls.push({ path, method, data })
    return { id: 'a'.repeat(32), config: data.config, status: 'draft' }
  })
  Object.assign(state.draft, { symbol: 'sz.000001', quantity: 300, limit_price: 10, threshold: 10, max_cash: 4000 })
  const reference = { run_id: 'b'.repeat(32), input_hash: 'c'.repeat(64), output_hash: 'd'.repeat(64) }
  state.applyResearchSeed({ symbol: 'sz.000001', reference })
  await state.create()
  assert.equal(calls.length, 0)
  assert.deepEqual(state.draft, { symbol: 'sz.000001', quantity: null, limit_price: null, threshold: null, max_cash: null })
  Object.assign(state.draft, { quantity: 100, limit_price: 10, threshold: 10, max_cash: 1200 })
  await state.create()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].path, '/strategy-versions')
  assert.deepEqual(calls[0].data.config.research_reference, reference)
  assert.equal(state.selected.status, 'draft')
})

test('funding preview survives a decision and stops warning after revocation', async () => {
  const version = { id: 'a'.repeat(32), config: { kind: 'buy_once_below' }, status: 'approved', config_sha256: 'b'.repeat(64), execution_allowed: false }
  const funding = { required_cash: '1005.01', available_cash: '1080.00', cap_sufficient: false, cash_sufficient: true }
  const state = mount(() => ({ ...version, status: 'revoked' }))
  state.versions = [{ ...version, funding }]
  state.selectedId = version.id
  assert.equal(state.capInsufficient, true)
  await state.decide(version, 'revoked')
  assert.equal(state.capInsufficient, false)
  assert.deepEqual(state.selected.funding, funding)
})

test('legacy buy and new sell versions have their own direction and buy-only funding display', () => {
  const state = mount(() => assert.fail('Direction must not call the API'))
  const buy = { id: 'a'.repeat(32), config: { kind: 'buy_once_below' }, funding: { required_cash: '1050.00' } }
  const sell = { id: 'b'.repeat(32), config: { kind: 'sell_once_above' }, funding: { notes: 'sell funding' } }
  state.versions = [buy, sell]
  assert.equal(state.strategySide(buy), 'buy')
  assert.equal(state.strategySide(sell), 'sell')
  assert.equal(state.strategySide({ config: {} }), 'unknown')
  state.selectedId = buy.id
  assert.equal(state.buyFunding, buy.funding)
  state.selectedId = sell.id
  assert.equal(state.buyFunding, null)
  assert.equal(state.capInsufficient, false)
})

test('refresh and revocation replace the persisted order status for the selected version', async () => {
  const version = { id: 'a'.repeat(32), status: 'approved', execution_allowed: true, order: null }
  const partial = { id: '90', status: 'partial', filled: 100, quantity: 200, fees: '5.01' }
  const calls = []
  const state = mount((path, method) => {
    calls.push([path, method])
    if (method === 'post') return { ...version, status: 'revoked', execution_allowed: false, order: { ...partial, status: 'cancelled' } }
    return { versions: [{ ...version, order: partial }] }
  })
  state.versions = [version]
  state.selectedId = version.id
  await state.refresh()
  assert.deepEqual(state.selected.order, partial)
  assert.deepEqual(calls, [['/strategy-versions', undefined]])
  await state.decide(state.selected, 'revoked')
  assert.equal(state.selected.order.status, 'cancelled')
  assert.equal(state.selected.order.fees, '5.01')
})

test('a notice opens only its exact strategy, including versions outside the latest page', async () => {
  const linked = { id: 'a'.repeat(32), status: 'draft', config_sha256: '1'.repeat(64) }
  const other = { id: 'b'.repeat(32), status: 'draft', config_sha256: '2'.repeat(64) }
  const calls = []
  let unavailable = false
  const state = mount((path, method) => {
    calls.push([path, method])
    if (path === '/strategy-versions') return { versions: [other] }
    if (unavailable) throw new Error('strategy_version_not_found')
    return linked
  })
  state.preferredId = linked.id
  await state.refresh()
  assert.equal(state.selected.id, linked.id)
  unavailable = true
  await state.refresh()
  assert.equal(state.selected, undefined)
  assert.equal(state.error, 'ashare.strategyLinkedVersionUnavailable')
  assert.ok(calls.every(([, method]) => method === undefined))
})

test('a viewer reads only the selected version assessment with an explicit user action', async () => {
  const version = { id: 'a'.repeat(32), status: 'draft', config_sha256: 'f'.repeat(64) }
  const evidence = { version_id: version.id, config_sha256: version.config_sha256, mode: 'paper',
    execution_allowed: false, execution: { status: 'not_activated' }, status: 'insufficient_evidence', proposal: null }
  const calls = []
  const state = mount((...args) => { calls.push(args); return evidence }, false)
  state.versions = [version]
  state.selectedId = version.id
  state.selectionChanged()
  assert.equal(calls.length, 0)
  await state.loadAssessment(state.selected)
  assert.deepEqual(calls, [[`/strategy-versions/${version.id}/evaluation`]])
  assert.equal(state.assessment, evidence)
  assert.equal(state.selected.status, 'draft')
  assert.equal(state.dialog, undefined)
})

test('switching versions discards an in-flight assessment and rejects a mismatched hash', async () => {
  const first = { id: 'a'.repeat(32), config_sha256: 'f'.repeat(64) }
  const second = { id: 'b'.repeat(32), config_sha256: '0'.repeat(64) }
  const observation = v => ({ version_id: v.id, config_sha256: v.config_sha256, mode: 'paper', execution_allowed: false,
    execution: { status: 'not_activated' } })
  let resolve
  const state = mount(path => path.includes(first.id)
    ? new Promise(done => { resolve = done })
    : { ...observation(second), config_sha256: first.config_sha256 })
  state.versions = [first, second]
  state.selectedId = first.id
  const pending = state.loadAssessment(state.selected)
  state.selectedId = second.id
  state.selectionChanged()
  resolve(observation(first))
  await pending
  assert.equal(state.assessment, null)
  assert.equal(state.assessmentError, '')
  await state.loadAssessment(state.selected)
  assert.equal(state.assessment, null)
  assert.equal(state.assessmentError, 'ashare.strategyEvaluationUnavailable')
})
