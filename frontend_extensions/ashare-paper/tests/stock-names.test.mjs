import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, mkdtemp, mkdir, writeFile, rm, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const overlay = new URL('../', import.meta.url)
const moduleFromSource = source => import('data:text/javascript;charset=utf-8,' + encodeURIComponent(source))
const translate = key => ({ 'marketContext.spot': '现货', 'marketContext.product.equity': '股票', 'marketContext.product.future': '期货' }[key] || key)

async function watchlistModule () {
  // Fixture: official QuantDinger-Vue 2ccc70b208141b3898060c4888853f83111ecfd8,
  // src/utils/watchlistAssets.js. Its license is ../UPSTREAM_LICENSE.txt.
  const directory = await mkdtemp(join(tmpdir(), 'watchlist-names-'))
  try {
    await mkdir(join(directory, 'src/utils'), { recursive: true })
    const target = join(directory, 'src/utils/watchlistAssets.js')
    await writeFile(target, await readFile(new URL('fixtures/upstream-watchlistAssets.js', import.meta.url)))
    const patch = fileURLToPath(new URL('watchlist-names.patch', overlay))
    try { await access(patch) } catch (_) { return moduleFromSource(await readFile(target, 'utf8')) }
    execFileSync('git', ['apply', '--check', patch], { cwd: directory })
    execFileSync('git', ['apply', patch], { cwd: directory })
    execFileSync('git', ['apply', '--reverse', '--check', patch], { cwd: directory })
    return moduleFromSource(await readFile(target, 'utf8'))
  } finally { await rm(directory, { recursive: true, force: true }) }
}

async function paperComponent () {
  const file = new URL('src/views/ashare-paper/index.vue', overlay)
  const source = await readFile(file, 'utf8')
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace("import { paperRequest } from '@/api/asharePaper'", "const paperRequest = () => { throw new Error('unexpected network call') }")
    .replace(/from '(\.\/[^']+)'/g, (_, path) => "from '" + new URL(path, file).href + "'")
  const { default: component } = await moduleFromSource(script)
  const context = { state: { settings: { symbols: ['sz.000001'] }, quotes: [{ symbol: 'sz.000001', name: '平安银行' }] }, $t: key => ({ 'ashare.nameUnavailable': '名称暂不可用', 'ashare.name': '名称' }[key] || key) }
  for (const [key, method] of Object.entries(component.methods)) context[key] = method.bind(context)
  return { source, component, context }
}

test('official watchlist preserves the security name alongside product context', async () => {
  const { watchlistContextLabel } = await watchlistModule()
  const row = { asset_id: 101, market: 'CNStock', symbol: '000001', name: '平安银行', product_type: 'equity', market_types: ['spot'] }
  assert.equal(watchlistContextLabel(row, translate), '平安银行 · 股票 · 现货')
  assert.equal(row.symbol, '000001')
})

test('official label preserves other markets, names without metadata, and honest missing-name fallbacks', async () => {
  const { watchlistContextLabel, watchlistAssetKey, watchlistPayload } = await watchlistModule()
  assert.equal(watchlistContextLabel({ symbol: 'NVDA', name: 'NVIDIA', product_type: 'equity' }, translate), 'NVIDIA · 股票')
  assert.equal(watchlistContextLabel({ symbol: 'BTC/USDT', name: 'Bitcoin', product_type: 'crypto' }, translate), 'Bitcoin')
  assert.equal(watchlistContextLabel({ symbol: '000001', name: '', product_type: 'equity' }, translate), '股票')
  assert.equal(watchlistContextLabel({ symbol: '000001', name: '' }, translate), '000001')
  assert.equal(watchlistContextLabel({ symbol: '000001', name: '  平安银行  ' }, translate), '平安银行')
  assert.equal(watchlistContextLabel(null, translate), '')
  assert.equal(watchlistAssetKey({ asset_id: 101, symbol: '000001' }), 'asset:101')
  assert.deepEqual(watchlistPayload({ asset_id: 101, symbol: '000001' }), { asset_id: 101 })
})

test('paper order selector renders the available quote name without changing the symbol value', async () => {
  const { source, context } = await paperComponent()
  const option = source.match(/<a-select-option v-for="symbol in symbols"[^>]*>([\s\S]*?)<\/a-select-option>/)
  assert.ok(option)
  const expression = option[1].match(/{{\s*([\s\S]*?)\s*}}/)[1]
  const label = new Function('symbol', 'with (this) { return ' + expression + ' }').call(context, 'sz.000001')
  assert.equal(label, 'sz.000001 · 平安银行')
  assert.ok(option[0].includes(':value="symbol"'))
})

test('paper saved symbols and positions use matching quote names, with an explicit unknown state', async () => {
  const { source, component, context } = await paperComponent()
  assert.ok(source.includes('class="paper-saved-symbols"'))
  assert.equal(context.symbolLabel('sh.600000'), 'sh.600000 · 名称暂不可用')
  context.state.positions = [{ symbol: 'sz.000001', quantity: 100 }, { symbol: 'sh.600000', quantity: 200 }]
  assert.deepEqual(component.computed.namedPositions.call(context), [
    { symbol: 'sz.000001', name: '平安银行', quantity: 100 },
    { symbol: 'sh.600000', name: '名称暂不可用', quantity: 200 }
  ])
  context.state.quotes = [{ symbol: 'sz.000001', name: '   ' }]
  assert.equal(context.symbolName('sz.000001'), '名称暂不可用')
  assert.equal(context.state.positions[0].name, undefined)
})
