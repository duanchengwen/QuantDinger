import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/views/ashare-paper/strategy-backtest.vue', import.meta.url), 'utf8')
const script = source.split('<script>')[1].split('</script>')[0]
  .replace("import { paperRequest } from '@/api/asharePaper'", '')
  .replace('export default', 'return')

const reference = { run_id: 'a'.repeat(32), result_sha256: 'b'.repeat(64) }
const version = { id: 'c'.repeat(32), config: { backtest_reference: reference } }

function candidate () {
  return {
    ...reference,
    status: 'candidate',
    reason_code: 'bearish_trend_settled_lot_available',
    evidence: {
      source: 'baostock', source_url: 'https://www.baostock.com/mainContent?file=history_K_data.md',
      first_session: '2026-08-14', last_session: '2026-11-18', warmup_sessions: 20, holdout_sessions: 40,
      historical_point_in_time_certified: false,
      receipts: [{ receipt_id: 'd'.repeat(64), source_sha256: 'e'.repeat(64), received_at: '2026-11-19T01:20:00+00:00' }]
    },
    metrics: {
      holdout_start: '2026-09-14', holdout_end: '2026-11-18', net_pnl: '-11.20', return_fraction: '-0.0112',
      max_drawdown_fraction: '0.035', buy_fills: 2, sell_fills: 2, closed_round_trips: 2,
      fee_policy: { commission_rate: '0.0003', minimum_commission: '5', transfer_rate: '0.00001', sell_tax_rate: '0.0005' }
    }
  }
}

function mount (request, selected = version) {
  const component = new Function('paperRequest', script)(request)
  const state = { ...component.data(), version: selected, $i18n: { locale: 'en-US' } }
  for (const [name, method] of Object.entries(component.methods)) state[name] = method.bind(state)
  for (const [name, getter] of Object.entries(component.computed)) {
    Object.defineProperty(state, name, { get: getter.bind(state) })
  }
  state.dispose = component.beforeDestroy.bind(state)
  return state
}

test('the version explicitly reads the matching historical report from the read-only route', async () => {
  const calls = []
  const state = mount((...args) => { calls.push(args); return candidate() })
  assert.deepEqual(calls, [])
  await state.loadReport()
  assert.deepEqual(calls, [[`/strategy-versions/${version.id}/backtest`]])
  assert.equal(state.report.metrics.holdout_start, '2026-09-14')
  assert.equal(state.report.evidence.receipts[0].source_sha256, 'e'.repeat(64))
  assert.equal(state.percent(state.report.metrics.return_fraction), '-1.12%')
  assert.equal(state.percent(state.report.metrics.fee_policy.transfer_rate), '0.001%')
  assert.equal(state.money(state.report.metrics.net_pnl), '-11.20')
  assert.equal(state.reasonLabel, 'strategyBacktestSellReason')
})

test('missing, mismatched or incomplete evidence never displays a return', async () => {
  const manual = mount(() => assert.fail('An unlinked strategy must not request a backtest'), { id: version.id, config: {} })
  await manual.loadReport()
  assert.equal(manual.reference, null)
  assert.equal(manual.unlinkedMessage, 'strategyBacktestAbsent')
  const corrupt = mount(() => assert.fail('A corrupt reference must fail before requesting a backtest'), {
    id: version.id, config: { backtest_reference: { ...reference, result_sha256: 'invalid' } }
  })
  await corrupt.loadReport()
  assert.equal(corrupt.unlinkedMessage, 'strategyBacktestUnavailable')
  for (const response of [null, { ...candidate(), result_sha256: 'f'.repeat(64) }, { ...candidate(), metrics: null }]) {
    const state = mount(() => response)
    await state.loadReport()
    assert.equal(state.failed, true)
    assert.equal(state.report, null)
  }
})

test('changing versions discards a pending backtest and prevents a duplicate read', async () => {
  let finish
  let calls = 0
  const state = mount(() => { calls += 1; return new Promise(resolve => { finish = resolve }) })
  const pending = state.loadReport()
  await state.loadReport()
  assert.equal(calls, 1)
  state.dispose()
  finish(candidate())
  await pending
  assert.equal(state.report, null)
})

test('source links accept the known BaoStock document and reject an untrusted target', async () => {
  const result = candidate()
  const state = mount(() => result)
  await state.loadReport()
  assert.equal(state.sourceUrl, result.evidence.source_url)
  result.evidence.source_url = 'javascript:alert(1)'
  assert.equal(state.sourceUrl, '')
})
