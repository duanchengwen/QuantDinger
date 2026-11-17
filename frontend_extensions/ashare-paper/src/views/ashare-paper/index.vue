<template>
  <main class="ashare-paper qd-workspace-page" :class="{ 'theme-dark': isDarkTheme }">
    <header class="paper-heading">
      <div><a-tag color="cyan">{{ t('paper') }}</a-tag><h1>{{ t('title') }}</h1><p>{{ t('subtitle') }}</p></div>
      <div class="paper-health"><a-tag :color="workerFresh ? 'green' : 'orange'">{{ t('worker') }}: {{ workerLabel }}</a-tag><br><small>{{ t('updated') }} {{ formatTime(updatedAt) }}</small><a-button :loading="loading" @click="refresh">{{ t('refresh') }}</a-button></div>
    </header>
    <a-alert v-if="error" type="error" show-icon :message="error" class="paper-alert" />
    <a-alert v-if="readOnly" type="info" :message="t('readOnly')" class="paper-alert" />
    <a-card v-if="!state.account" :title="t('initialize')" class="paper-section">
      <a-form layout="inline" @submit.prevent="initialize">
        <a-form-item :label="t('capital')"><a-input-number v-model="capital" :min="100" :max="100000000" :precision="2" /></a-form-item>
        <a-form-item><a-button html-type="submit" type="primary" :disabled="readOnly" :loading="busy">{{ t('initialize') }}</a-button></a-form-item>
      </a-form>
    </a-card>
    <section v-else class="paper-metrics">
      <a-card v-for="metric in metrics" :key="metric.key"><span>{{ t(metric.label) }}</span><strong>{{ money(state.account[metric.key]) }}</strong><small>{{ t('currency') }}</small></a-card>
    </section>

    <a-card :title="t('quotes')" class="paper-section">
      <small slot="extra">{{ t('quoteHint') }}</small>
      <a-table
        :columns="quoteColumns"
        :data-source="state.quotes || []"
        row-key="symbol"
        :pagination="false"
        :scroll="{ x: 850 }"
        size="middle">
        <template slot="symbol" slot-scope="value, row"><a @click="selectSymbol(row)">{{ value }}</a><br><small>{{ row.name }}</small></template>
        <template slot="depthBid" slot-scope="value, row">{{ row.bid }} / {{ row.bid_size }}</template>
        <template slot="depthAsk" slot-scope="value, row">{{ row.ask }} / {{ row.ask_size }}</template>
        <template slot="time" slot-scope="value, row"><span>{{ formatTime(row.exchange_time) }}</span><br><small>{{ t('received') }} {{ formatTime(row.received_at) }}</small></template>
        <template slot="quality" slot-scope="value, row"><a-tag :color="quoteFresh(row) ? 'green' : 'orange'">{{ quoteFresh(row) ? t('fresh') : t('stale') }}</a-tag></template>
        <template slot="rules" slot-scope="value, row"><small>{{ ruleReady(row.symbol) ? t('reviewed') : t('unreviewed') }}</small><br><a-button size="small" :disabled="readOnly || !state.account" @click="openReview(row)">{{ t('review') }}</a-button></template>
        <template slot="emptyText">{{ t('emptyMarket') }}</template>
      </a-table>
    </a-card>

    <div class="paper-workspace">
      <a-card :title="t('order')">
        <a-form layout="vertical" @submit.prevent="confirmOrder">
          <a-form-item :label="t('symbol')"><a-select v-model="order.symbol" @change="orderChanged"><a-select-option v-for="symbol in symbols" :key="symbol" :value="symbol">{{ symbol }}</a-select-option></a-select></a-form-item>
          <a-form-item :label="t('side')"><a-radio-group v-model="order.side" button-style="solid" @change="orderChanged"><a-radio-button value="buy">{{ t('buy') }}</a-radio-button><a-radio-button value="sell">{{ t('sell') }}</a-radio-button></a-radio-group></a-form-item>
          <div class="paper-pair"><a-form-item :label="t('quantity')"><a-input-number v-model="order.quantity" :min="100" :step="100" :precision="0" @change="orderChanged" /></a-form-item><a-form-item :label="t('price')"><a-input-number v-model="order.limit_price" :min="0.01" :step="0.01" :precision="2" @change="orderChanged" /></a-form-item></div>
          <p class="paper-muted">{{ t('orderHelp') }}</p>
          <a-alert v-if="!canOrder" type="warning" :message="t('gate')" class="paper-alert" />
          <a-button
            type="primary"
            html-type="submit"
            block
            :disabled="!canOrder || busy"
            :loading="busy"
            data-testid="submit-paper-order">{{ t('submit') }}</a-button>
        </a-form>
      </a-card>
      <a-card :title="t('positions')">
        <a-table :columns="positionColumns" :data-source="state.positions || []" :row-key="row => row.symbol + row.acquired_day" size="small" :pagination="{ pageSize: 8 }">
          <template slot="emptyText">{{ t('noData') }}</template>
        </a-table>
      </a-card>
    </div>

    <a-card class="paper-section">
      <p v-if="historyTruncated" class="paper-muted">{{ t('historyBounded') }}</p>
      <a-tabs v-model="activeTab" @change="onTabChange">
        <a-tab-pane key="orders" :tab="t('orders')">
          <a-table
            :columns="orderColumns"
            :data-source="[...(state.orders || [])].reverse()"
            row-key="id"
            size="small"
            :pagination="{ pageSize: 8 }"
            :scroll="{ x: 750 }">
            <template slot="side" slot-scope="value">{{ t(value) }}</template><template slot="status" slot-scope="value">{{ t('state.' + value) }}</template>
            <template slot="cancel" slot-scope="value, row"><a-button v-if="['open', 'partial'].includes(row.status)" size="small" :disabled="readOnly || busy" @click="cancelOrder(row.id)">{{ t('cancel') }}</a-button></template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="reports" :tab="t('reports')">
          <p class="paper-muted">{{ t('reportHelp') }}</p>
          <a-alert v-if="reportsError" type="error" :message="reportsError" class="paper-alert" />
          <a-button :loading="reportsLoading" @click="loadReports">{{ t('refreshReports') }}</a-button>
          <a-table
            :columns="reportColumns"
            :data-source="reportRows"
            row-key="trading_day"
            size="small"
            :pagination="{ pageSize: 8 }"
            :scroll="{ x: 800 }">
            <template slot="money" slot-scope="value">{{ value == null ? '—' : money(value) }}</template>
            <template slot="reconciliation" slot-scope="value"><a-tag :color="value === true ? 'green' : 'red'">{{ value === true ? t('reconciled') : t('reconciliationFailed') }}</a-tag></template>
            <template slot="time" slot-scope="value">{{ formatTime(value) }}</template>
            <template slot="emptyText">{{ t('noReports') }}</template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="fills" :tab="t('fills')">
          <a-table
            :columns="fillColumns"
            :data-source="[...(state.fills || [])].reverse()"
            row-key="id"
            size="small"
            :pagination="{ pageSize: 8 }"
            :scroll="{ x: 750 }">
            <template slot="side" slot-scope="value">{{ t(value) }}</template><template slot="status" slot-scope="value">{{ t('state.' + value) }}</template><template slot="time" slot-scope="value">{{ formatTime(value) }}</template>
          </a-table>
        </a-tab-pane>
        <a-tab-pane key="settings" :tab="t('settings')">
          <a-form layout="vertical" @submit.prevent="saveSettings"><a-form-item :label="t('symbols')"><a-input v-model="settingsSymbols" placeholder="sz.000001,sh.600000" /></a-form-item><a-form-item :label="t('interval')"><a-input-number v-model="settingsInterval" :min="2" :max="3600" /></a-form-item><a-button html-type="submit" :disabled="readOnly || !state.account" :loading="busy">{{ t('save') }}</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="feishu" :tab="t('feishu')">
          <div class="paper-bot"><h3>{{ t('feishu') }} <a-tag :color="botConfigured ? 'green' : undefined">{{ botConfigured ? t('configured') : t('notConfigured') }}</a-tag><a-tag v-if="state.notifications && state.notifications.signing_enabled">{{ t('signingOn') }}</a-tag></h3>
            <p>{{ t('feishuHint') }}</p><ol class="paper-steps"><li>{{ t('feishuStep1') }}</li><li>{{ t('feishuStep2') }}</li><li>{{ t('feishuStep3') }}</li></ol>
            <p><a href="https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot" target="_blank" rel="noopener noreferrer">{{ t('feishuGuide') }}</a></p>
            <a-form layout="vertical" @submit.prevent="saveBot"><a-form-item :label="t('webhook')"><a-input-password v-model="bot.webhook_url" autocomplete="new-password" /></a-form-item><a-form-item :label="t('signing')"><a-input-password v-model="bot.signing_secret" autocomplete="new-password" /></a-form-item><p class="paper-muted">{{ t('secretHelp') }}</p><a-button html-type="submit" :disabled="readOnly || !bot.webhook_url || !state.account" :loading="busy">{{ t('saveBot') }}</a-button> <a-button :disabled="readOnly || !botConfigured" :loading="busy" @click="testBot">{{ t('testBot') }}</a-button> <a-button type="danger" :disabled="readOnly || !botConfigured || busy" @click="confirmDisableBot">{{ t('disableBot') }}</a-button></a-form>
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-card>

    <a-modal
      :visible="reviewVisible"
      :title="t('reviewTitle')"
      :confirm-loading="busy"
      :ok-button-props="{ props: { disabled: !reviewChecked || !review.evidence.trim() } }"
      @ok="saveReview"
      @cancel="reviewVisible = false">
      <a-alert type="info" :message="t('reviewNote')" class="paper-alert" /><p>{{ review.symbol }} · {{ review.trading_day }}</p>
      <a-form layout="vertical"><div class="paper-pair"><a-form-item :label="t('lower')"><a-input-number v-model="review.limit_down" :min="0.01" :precision="2" /></a-form-item><a-form-item :label="t('upper')"><a-input-number v-model="review.limit_up" :min="0.01" :precision="2" /></a-form-item></div><a-form-item :label="t('evidence')"><a-textarea v-model="review.evidence" :rows="3" /></a-form-item><a-checkbox v-model="reviewChecked">{{ t('reviewCheck') }}</a-checkbox></a-form>
    </a-modal>
  </main>
</template>

<script>
import { paperRequest } from '@/api/asharePaper'
import { ageSeconds, quoteIsFresh, shanghaiDate, ruleIsReady, roleIsViewer } from './state.mjs'

const emptyState = () => ({ account: null, quotes: [], orders: [], positions: [], fills: [], rules: {}, settings: {}, worker: {}, notifications: {} })
export default {
  name: 'AsharePaper',
  data () {
    return {
 state: emptyState(),
capital: 100000,
loading: false,
busy: false,
error: '',
updatedAt: null,
now: Date.now(),
timer: null,
clock: null,
stopped: false,
      settingsSymbols: 'sz.000001',
settingsInterval: 5,
settingsLoaded: false,
      order: { symbol: 'sz.000001', side: 'buy', quantity: 100, limit_price: null },
orderId: '',
      reviewVisible: false,
reviewChecked: false,
review: { symbol: '', trading_day: '', evidence: '', limit_down: null, limit_up: null },
      bot: { webhook_url: '', signing_secret: '' },
      activeTab: 'orders',
reports: [],
reportsLoading: false,
reportsError: ''
}
  },
  computed: {
    isDarkTheme () { return ['dark', 'realdark'].includes(this.$store.getters.theme) },
    metrics () { return [{ key: 'cash', label: 'cash' }, { key: 'available_cash', label: 'available' }, { key: 'reserved_cash', label: 'reserved' }, { key: 'total_fees', label: 'fees' }] },
    symbols () { return this.state.settings.symbols || ['sz.000001'] },
    readOnly () { const info = this.$store.state.user.info || {}; return roleIsViewer(info.role) },
    workerFresh () { const worker = this.state.worker || {}; const age = this.age(worker.observed_at); return worker.state === 'healthy' && age >= 0 && age <= 30 },
    workerLabel () { return this.workerFresh ? this.t('state.healthy') : (this.state.worker.state === 'healthy' ? this.t('stale') : this.state.worker.state ? this.t('state.' + this.state.worker.state) : this.t('unknown')) },
    canOrder () { const q = this.state.quotes.find(q => q.symbol === this.order.symbol); return !this.readOnly && !!this.state.account && !this.error && this.workerFresh && this.quoteFresh(q) && this.ruleReady(this.order.symbol) && this.order.quantity > 0 && this.order.quantity % 100 === 0 && Number(this.order.limit_price) > 0 },
    botConfigured () { return !!(this.state.notifications && this.state.notifications.configured) },
    quoteColumns () { return [{ title: this.t('symbol'), dataIndex: 'symbol', scopedSlots: { customRender: 'symbol' } }, { title: this.t('last'), dataIndex: 'last' }, { title: this.t('bid'), key: 'bid', scopedSlots: { customRender: 'depthBid' } }, { title: this.t('ask'), key: 'ask', scopedSlots: { customRender: 'depthAsk' } }, { title: this.t('time'), key: 'time', scopedSlots: { customRender: 'time' } }, { title: this.t('status'), key: 'quality', scopedSlots: { customRender: 'quality' } }, { title: this.t('rules'), key: 'rules', scopedSlots: { customRender: 'rules' } }] },
    positionColumns () { return this.columns(['symbol', 'quantity', 'acquired_day']) },
    orderColumns () { return [...this.columns(['id', 'symbol', 'side', 'quantity', 'filled', 'limit_price', 'status']), { title: this.t('action'), key: 'cancel', scopedSlots: { customRender: 'cancel' } }] },
    historyTruncated () { return Object.values((this.state.history || {}).truncated || {}).some(Boolean) },
    reportRows () { return this.reports.map(item => ({ ...item.report, trading_day: item.trading_day, generated_at: item.generated_at, reconciliation_ok: item.report.reconciliation && item.report.reconciliation.ok, observation: item.report.observation_summary && item.report.observation_summary.poll_count != null ? `${item.report.observation_summary.wall_clock_polls} / ${item.report.observation_summary.controlled_time_polls}` : '—' })) },
    reportColumns () {
 return [
      { title: this.t('reportDay'), dataIndex: 'trading_day' },
      { title: this.t('reportCash'), dataIndex: 'cash_end', scopedSlots: { customRender: 'money' } },
      { title: this.t('reportBuys'), dataIndex: 'buy_notional', scopedSlots: { customRender: 'money' } },
      { title: this.t('reportSells'), dataIndex: 'sell_notional', scopedSlots: { customRender: 'money' } },
      { title: this.t('fees'), dataIndex: 'fees', scopedSlots: { customRender: 'money' } },
      { title: this.t('reportFills'), dataIndex: 'day_fill_count' },
      { title: this.t('reconciliation'), dataIndex: 'reconciliation_ok', scopedSlots: { customRender: 'reconciliation' } },
      { title: this.t('reportObservation'), dataIndex: 'observation' },
      { title: this.t('reportGenerated'), dataIndex: 'generated_at', scopedSlots: { customRender: 'time' } }
    ]
},
    fillColumns () { return this.columns(['order_id', 'symbol', 'side', 'quantity', 'price', 'fee', 'exchange_time']) }
  },
  mounted () { this.clock = setInterval(() => { this.now = Date.now() }, 1000); this.poll() },
  beforeDestroy () { this.stopped = true; clearTimeout(this.timer); clearInterval(this.clock) },
  methods: {
    t (key, values) { return this.$t('ashare.' + key, values) },
    errorMessage (err) { const message = (err.response && err.response.data && err.response.data.msg) || err.message || this.t('error'); const keys = { auth_service_unavailable: 'service', paper_database_unavailable: 'service', paper_database_not_configured: 'service', paper_worker_stale: 'worker', paper_worker_not_healthy: 'worker', outside_continuous_auction: 'session', not_trading_day: 'session', fresh_quote_required: 'quote', 'stale quote': 'quote', 'insufficient available cash': 'cash', 'insufficient T+1 available shares': 'shares' }; return keys[message] ? this.t('error.' + keys[message]) : message },
    columns (keys) { const names = { acquired_day: 'acquired', limit_price: 'price', fee: 'fees', order_id: 'id', exchange_time: 'time' }; return keys.map(key => ({ title: this.t(names[key] || key), dataIndex: key, ...(['side', 'status'].includes(key) ? { scopedSlots: { customRender: key } } : {}), ...(key === 'exchange_time' ? { scopedSlots: { customRender: 'time' } } : {}) })) },
    money (value) { return Number(value || 0).toLocaleString(this.$i18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    formatTime (value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(this.$i18n.locale, { timeZone: 'Asia/Shanghai', hour12: false }) },
    age (value) { return ageSeconds(value, this.now) },
    quoteFresh (quote) { return quoteIsFresh(quote, this.now) },
    today () { return shanghaiDate(this.now) },
    ruleReady (symbol) { return ruleIsReady((this.state.rules || {})[symbol], this.now) },
    onTabChange (key) { if (key === 'reports') this.loadReports() },
    async loadReports () { if (this.reportsLoading || this.stopped) return; this.reportsLoading = true; try { const result = await paperRequest('/daily-reports?limit=30'); if (!this.stopped) { this.reports = result; this.reportsError = '' } } catch (err) { this.reportsError = this.errorMessage(err) } finally { this.reportsLoading = false } },
    async refresh () { if (this.loading || this.stopped) return; this.loading = true; try { const data = await paperRequest(); if (this.stopped) return; this.now = Date.now(); this.state = { ...emptyState(), ...data }; this.error = ''; this.updatedAt = new Date().toISOString(); if (!this.settingsLoaded) { this.settingsSymbols = this.symbols.join(','); this.settingsInterval = this.state.settings.interval_seconds || 5; this.order.symbol = this.symbols[0]; this.settingsLoaded = true } } catch (err) { this.error = this.errorMessage(err) } finally { this.loading = false } },
    async poll () { if (this.stopped) return; if (!document.hidden) await this.refresh(); if (!this.stopped) this.timer = setTimeout(() => this.poll(), 3000) },
    async mutate (path, method, data, message) { if (this.busy) return; this.busy = true; try { const result = await paperRequest(path, method, data); if (message) this.$message.success(message); await this.refresh(); return result } catch (err) { this.error = this.errorMessage(err); throw err } finally { this.busy = false } },
    async initialize () { try { await this.mutate('/initialize', 'post', { initial_cash: Number(this.capital).toFixed(2) }, this.t('saved')) } catch (_) {} },
    selectSymbol (quote) { this.order.symbol = quote.symbol; this.order.limit_price = Number(this.order.side === 'buy' ? quote.ask : quote.bid); this.orderChanged() },
    orderChanged () { this.orderId = '' },
    confirmOrder () { if (!this.canOrder || this.busy) return; this.$confirm({ title: this.t('confirm'), content: this.t('confirmBody', { side: this.t(this.order.side), symbol: this.order.symbol, quantity: this.order.quantity, price: this.order.limit_price }), onOk: () => this.submitOrder() }) },
    async submitOrder () { if (!this.canOrder) return; if (!this.orderId) this.orderId = 'web-' + (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2)); try { await this.mutate('/orders', 'post', { ...this.order, limit_price: Number(this.order.limit_price).toFixed(2), client_order_id: this.orderId }, this.t('submitted')); this.orderId = '' } catch (_) {} },
    async cancelOrder (id) { try { await this.mutate(`/orders/${id}/cancel`, 'post', {}, this.t('cancelled')) } catch (_) {} },
    openReview (quote) { this.reviewChecked = false; this.review = { symbol: quote.symbol, trading_day: this.today(), evidence: '', limit_down: Number(quote.limit_down), limit_up: Number(quote.limit_up) }; this.reviewVisible = true },
    async saveReview () { if (!this.reviewChecked || !this.review.evidence.trim()) return; try { await this.mutate('/rules/review', 'post', { ...this.review, limit_down: Number(this.review.limit_down).toFixed(2), limit_up: Number(this.review.limit_up).toFixed(2), suspended: false, st: false }, this.t('saved')); this.reviewVisible = false } catch (_) {} },
    async saveSettings () { try { await this.mutate('/settings', 'put', { symbols: this.settingsSymbols.split(',').map(s => s.trim()).filter(Boolean), interval_seconds: this.settingsInterval }, this.t('saved')) } catch (_) {} },
    async saveBot () { try { await this.mutate('/notifications/config', 'post', { ...this.bot }, this.t('saved')); this.bot = { webhook_url: '', signing_secret: '' } } catch (_) {} },
    confirmDisableBot () { this.$confirm({ title: this.t('disableBot'), content: this.t('disableBotConfirm'), onOk: async () => { try { await this.mutate('/notifications/config', 'post', { webhook_url: '', signing_secret: '' }, this.t('saved')); this.bot = { webhook_url: '', signing_secret: '' } } catch (_) {} } }) },
    async testBot () { try { const result = await this.mutate('/notifications/test', 'post', {}); if (result && result.status === 'sent') this.$message.success(this.t('botSent')); else this.$message.warning(this.t('botFailed')) } catch (_) {} }
  }
}
</script>

<style scoped>
.ashare-paper { color: var(--qd-text); max-width: 1500px; margin: 0 auto; padding: 12px 8px 32px; }
.paper-heading { display: flex; justify-content: space-between; gap: 24px; margin: 0 0 24px; }
.paper-heading h1 { margin: 12px 0 4px; font-size: 26px; color: inherit; }
.paper-heading p, .paper-muted { color: var(--qd-text-secondary); line-height: 1.65; }
.paper-health { text-align: right; min-width: 180px; }
.paper-health button { display: block; margin: 12px 0 0 auto; }
.paper-health small { line-height: 2; color: var(--qd-text-secondary); }
.paper-alert { margin: 0 0 16px; }
.paper-section { margin-bottom: 20px; }
.paper-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.paper-metrics strong { color: var(--qd-text); display: block; font-size: 28px; font-variant-numeric: tabular-nums; margin: 8px 0; }
.paper-metrics span, .paper-metrics small { color: var(--qd-text-secondary); }
.paper-workspace { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.6fr); gap: 20px; margin-bottom: 20px; }
.paper-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.paper-pair .ant-input-number { width: 100%; }
.paper-bot h3 { color: var(--qd-text); }
.paper-bot p, .paper-bot li { color: var(--qd-text-secondary); }
.paper-bot { max-width: 720px; padding: 4px; }
.paper-steps { list-style: none; padding: 0; line-height: 2; }
small { font-size: 12px; color: var(--qd-text-secondary); }
@media (max-width: 900px) { .paper-metrics { grid-template-columns: repeat(2, 1fr); } .paper-workspace { grid-template-columns: 1fr; } .paper-heading { flex-wrap: wrap; } .paper-health { text-align: left; } .paper-health button { margin-left: 0; } }
</style>
