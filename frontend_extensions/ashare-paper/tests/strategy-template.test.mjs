import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/views/ashare-paper/strategy-template.vue', import.meta.url), 'utf8')
const script = source.split('<script>')[1].split('</script>')[0]
  .replace("import { paperRequest } from '@/api/asharePaper'", '')
  .replace('export default', 'return')

const watched = 'sz.000001'
const removed = 'sh.600000'
const prices = { trigger_price: '9.80', limit_price: '10.20', max_cash: '1050.00' }

function mount (request, props) {
  const component = new Function('paperRequest', script)(request)
  const state = {
    ...component.data(), active: true, accountReady: true, canWrite: true, symbols: [watched],
    symbolLabel: symbol => symbol === watched ? 'sz.000001 · 平安银行' : symbol,
    $t: (key, params) => params ? `${key} ${params.symbol}` : key,
    $nextTick: callback => callback(),
    ...props
  }
  for (const [name, method] of Object.entries(component.methods)) state[name] = method.bind(state)
  for (const [name, getter] of Object.entries(component.computed)) {
    Object.defineProperty(state, name, { get: getter.bind(state) })
  }
  return state
}

function watchedTemplate (enabled = true) {
  return { enabled, symbol_scope: 'watched', symbols: [], ...prices }
}

test('first load defaults to disabled and a watched template saves settings only', async () => {
  const calls = []
  const state = mount((...args) => {
    calls.push(args)
    return args.length === 1 ? { revision: 0, template: null } : { revision: 1, template: watchedTemplate() }
  })
  await state.refresh()
  assert.equal(state.draft.enabled, false)
  assert.equal(state.canSave, false)
  assert.deepEqual(calls, [['/strategy-template']])
  Object.assign(state.draft, prices, { enabled: true, symbols: [watched] })
  await state.save()
  assert.deepEqual(calls[1], ['/strategy-template', 'put', {
    expected_revision: 0,
    template: watchedTemplate()
  }])
  assert.equal(calls.length, 2)
  assert.equal(state.savedTemplate.enabled, true)
  assert.equal(state.revision, 1)
  assert.equal(state.saved, true)
})

test('known names display in the picker and removed symbols still allow disabling', async () => {
  const original = { ...watchedTemplate(), symbol_scope: 'selected', symbols: [watched, removed] }
  const calls = []
  const state = mount((...args) => {
    calls.push(args)
    return args.length === 1 ? { revision: 6, template: original } : { revision: 7, template: args[2].template }
  })
  await state.refresh()
  assert.deepEqual(state.symbolOptions, [
    { value: watched, label: 'sz.000001 · 平安银行' },
    { value: removed, label: 'ashare.strategyTemplateRemovedSymbol sh.600000' }
  ])
  assert.equal(state.validationError, 'strategyTemplateSymbolsNotWatched')
  await state.save()
  assert.equal(calls.length, 1)
  state.draft.enabled = false
  await state.save()
  assert.equal(calls[1][2].expected_revision, 6)
  assert.deepEqual(calls[1][2].template, { ...original, enabled: false })
  state.draft.enabled = true
  state.draft.symbols = [watched]
  assert.equal(state.canSave, true)
})

test('a conflict preserves the unsaved settings, blocks repeated writes and reloads the newer revision', async () => {
  let putCount = 0
  let reads = 0
  const state = mount((path, method) => {
    if (method === 'put') {
      putCount += 1
      throw { response: { status: 409, data: { msg: 'strategy_template_conflict' } } }
    }
    reads += 1
    return { revision: reads, template: { ...watchedTemplate(), max_cash: reads === 1 ? '1050.00' : '1080.00' } }
  })
  await state.refresh()
  state.draft.max_cash = '1060.00'
  await state.save()
  assert.equal(state.error, 'ashare.strategyTemplateConflict')
  assert.equal(state.draft.max_cash, '1060.00')
  assert.equal(state.savedTemplate.max_cash, '1050.00')
  await state.save()
  assert.equal(putCount, 1)
  await state.refresh()
  assert.equal(state.error, '')
  assert.equal(state.revision, 2)
  assert.equal(state.draft.max_cash, '1080.00')
})

test('an earlier response cannot replace data after the strategy tab reopens', async () => {
  let finish
  let reads = 0
  const state = mount(() => {
    reads += 1
    if (reads === 1) return new Promise(resolve => { finish = resolve })
    return { revision: 7, template: watchedTemplate() }
  })
  const first = state.syncPresence()
  state.active = false
  state.syncPresence()
  state.active = true
  await state.syncPresence()
  finish({ revision: 0, template: null })
  await first
  assert.equal(state.revision, 7)
  assert.equal(state.draft.enabled, true)
})

test('missing accounts, auth errors, malformed data and viewer access cannot write', async () => {
  const missing = mount(() => assert.fail('Request without a paper account'), { accountReady: false })
  await missing.syncPresence()
  await missing.save()
  const unauthorized = mount(() => { throw { response: { status: 401 } } })
  await unauthorized.refresh()
  assert.equal(unauthorized.error, 'ashare.strategyTemplateUnauthorized')
  assert.equal(unauthorized.canSave, false)
  const malformed = mount(() => ({ revision: '0', template: null }))
  await malformed.refresh()
  assert.equal(malformed.error, 'ashare.strategyTemplateLoadFailed')
  assert.equal(malformed.canSave, false)
  const viewer = mount((path, method) => {
    assert.equal(method, undefined)
    return { revision: 1, template: watchedTemplate() }
  }, { canWrite: false })
  await viewer.refresh()
  assert.equal(viewer.savedTemplate.enabled, true)
  await viewer.save()
})

test('the form validates decimal syntax and explains the server fee check', async () => {
  const state = mount((path, method) => {
    if (method === 'put') throw new Error('strategy_cash_cap_exceeded')
    return { revision: 1, template: watchedTemplate() }
  })
  await state.refresh()
  state.draft.trigger_price = '9.801'
  assert.equal(state.validationError, 'strategyTemplateMoneyInvalid')
  state.draft.trigger_price = '9.80'
  state.draft.max_cash = '1020.00'
  assert.equal(state.validationError, 'strategyTemplateCashTooSmall')
  state.draft.max_cash = '1020.01'
  await state.save()
  assert.equal(state.error, 'ashare.strategyTemplateCashTooSmall')
  assert.equal(state.saved, false)
  assert.equal(state.savedTemplate.max_cash, '1050.00')
})

test('paper buy shortcut scrolls to the template and an exact strategy link keeps priority', () => {
  const page = readFileSync(new URL('../src/views/ashare-paper/index.vue', import.meta.url), 'utf8')
  const pageScript = page.split('<script>')[1].split('</script>')[0]
    .replace(/^import .*$/gm, '')
    .replace('export default', 'return')
  const component = new Function('researchCitations', pageScript)(() => [])
  const calls = []
  let ready = false
  const context = {
    activeTab: 'research',
    $route: { query: { tab: 'strategy-versions' } },
    $refs: { strategyTemplateSection: { querySelector: () => ready, scrollIntoView: options => calls.push(options) } },
    $nextTick: callback => callback(),
    syncResearchVisibility: () => calls.push('sync')
  }
  context.scrollToStrategyTemplate = component.methods.scrollToStrategyTemplate.bind(context)
  Object.defineProperty(context, 'linkedStrategy', { get: component.computed.linkedStrategy.bind(context) })
  assert.equal(component.computed.linkedTemplate.call(context), true)
  component.methods.showStrategyTemplate.call(context)
  assert.equal(context.activeTab, 'strategy-versions')
  assert.deepEqual(calls, ['sync'])
  ready = true
  context.scrollToStrategyTemplate()
  context.scrollToStrategyTemplate()
  assert.deepEqual(calls, ['sync', { block: 'start' }])
  context.$route.query.strategy_version = 'a'.repeat(32)
  assert.equal(component.computed.linkedTemplate.call(context), false)
  context.activeTab = 'orders'
  component.watch.linkedStrategy.handler.call(context, context.linkedStrategy)
  assert.equal(context.activeTab, 'strategy-versions')
  assert.deepEqual(calls, ['sync', { block: 'start' }])
})
