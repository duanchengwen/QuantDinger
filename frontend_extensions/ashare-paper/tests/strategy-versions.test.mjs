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
