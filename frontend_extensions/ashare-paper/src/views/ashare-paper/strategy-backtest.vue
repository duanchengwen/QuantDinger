<template>
  <section class="strategy-backtest" :aria-label="t('strategyBacktestTitle')">
    <h4>{{ t('strategyBacktestTitle') }}</h4>
    <p v-if="!reference">{{ t(unlinkedMessage) }}</p>
    <template v-else>
      <a-button :loading="loading" @click="loadReport">{{ t('strategyBacktestLoad') }}</a-button>
      <a-alert v-if="failed" type="warning" show-icon role="alert" :message="t('strategyBacktestUnavailable')" />
      <template v-if="report">
        <p>{{ t('strategyBacktestHistorical') }}</p>
        <p v-if="!report.evidence.historical_point_in_time_certified">{{ t('strategyBacktestPointInTime') }}</p>
        <p v-if="reasonLabel">{{ t(reasonLabel) }}</p>
        <dl>
          <dt>{{ t('source') }}</dt>
          <dd><a v-if="sourceUrl" :href="sourceUrl" target="_blank" rel="noopener noreferrer">{{ report.evidence.source }}</a><span v-else>{{ report.evidence.source }}</span></dd>
          <dt>{{ t('strategyBacktestHistoryWindow') }}</dt>
          <dd>{{ report.evidence.first_session }} – {{ report.evidence.last_session }}</dd>
          <dt>{{ t('strategyBacktestWarmup') }}</dt><dd>{{ t('strategyBacktestSessions', { count: report.evidence.warmup_sessions }) }}</dd>
          <dt>{{ t('strategyBacktestHoldout') }}</dt>
          <dd>{{ report.metrics.holdout_start }} – {{ report.metrics.holdout_end }} · {{ t('strategyBacktestSessions', { count: report.evidence.holdout_sessions }) }}</dd>
          <dt>{{ t('strategyBacktestNetPnl') }}</dt><dd>{{ money(report.metrics.net_pnl) }}</dd>
          <dt>{{ t('strategyBacktestReturn') }}</dt><dd>{{ percent(report.metrics.return_fraction) }}</dd>
          <dt>{{ t('strategyBacktestDrawdown') }}</dt><dd>{{ percent(report.metrics.max_drawdown_fraction) }}</dd>
          <dt>{{ t('strategyBacktestFills') }}</dt>
          <dd>{{ t('strategyBacktestFillCounts', { buy: report.metrics.buy_fills, sell: report.metrics.sell_fills, trips: report.metrics.closed_round_trips }) }}</dd>
        </dl>
        <details>
          <summary>{{ t('strategyBacktestFees') }}</summary>
          <dl>
            <dt>{{ t('strategyBacktestFeeCommission') }}</dt><dd>{{ percent(report.metrics.fee_policy.commission_rate) }}</dd>
            <dt>{{ t('strategyBacktestFeeMinimum') }}</dt><dd>{{ money(report.metrics.fee_policy.minimum_commission) }}</dd>
            <dt>{{ t('strategyBacktestFeeTransfer') }}</dt><dd>{{ percent(report.metrics.fee_policy.transfer_rate) }}</dd>
            <dt>{{ t('strategyBacktestFeeSellTax') }}</dt><dd>{{ percent(report.metrics.fee_policy.sell_tax_rate) }}</dd>
          </dl>
        </details>
        <details v-if="report.evidence.receipts.length">
          <summary>{{ t('strategyBacktestReceipts') }}</summary>
          <ul>
            <li v-for="receipt in report.evidence.receipts" :key="receipt.receipt_id">
              {{ receipt.session ? receipt.session + ' · ' : '' }}{{ receipt.received_at }}<br>
              <code>{{ receipt.receipt_id }}</code><br>
              <code>{{ receipt.source_sha256 }}</code>
            </li>
          </ul>
        </details>
      </template>
    </template>
  </section>
</template>

<script>
import { paperRequest } from '@/api/asharePaper'

const runId = /^[a-f0-9]{32}$/
const sha256 = /^[a-f0-9]{64}$/
const receiptId = /^(?:[a-f0-9]{32}|[a-f0-9]{64})$/
const tradingDate = /^\d{4}-\d{2}-\d{2}$/
const feeFields = ['commission_rate', 'transfer_rate', 'sell_tax_rate', 'minimum_commission']
const amountFields = ['net_pnl', 'return_fraction', 'max_drawdown_fraction']
const countFields = ['buy_fills', 'sell_fills', 'closed_round_trips']

function isDecimal (value) {
  return typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value) && Number.isFinite(Number(value))
}

function matches (pattern, value) { return typeof value === 'string' && pattern.test(value) }

function verifiedReport (report, reference) {
  if (report?.status !== 'candidate' || report.run_id !== reference.run_id || report.result_sha256 !== reference.result_sha256) return false
  const { evidence, metrics } = report
  if (!evidence || !metrics?.fee_policy || typeof evidence.source !== 'string' || !evidence.source ||
      !Array.isArray(evidence.receipts) || !evidence.receipts.length || typeof evidence.historical_point_in_time_certified !== 'boolean') return false
  const dates = [evidence.first_session, metrics.holdout_start, metrics.holdout_end, evidence.last_session]
  if (!dates.every((date, index) => matches(tradingDate, date) && (index === 0 || dates[index - 1] <= date)) ||
      !['warmup_sessions', 'holdout_sessions'].every(field => Number.isSafeInteger(evidence[field]) && evidence[field] > 0) ||
      !countFields.every(field => Number.isSafeInteger(metrics[field]) && metrics[field] >= 0) ||
      !amountFields.every(field => isDecimal(metrics[field])) ||
      !feeFields.every(field => isDecimal(metrics.fee_policy[field]) && Number(metrics.fee_policy[field]) >= 0)) return false
  return evidence.receipts.every(receipt => receipt && matches(receiptId, receipt.receipt_id) && matches(sha256, receipt.source_sha256) &&
    typeof receipt.received_at === 'string' && Number.isFinite(Date.parse(receipt.received_at)))
}

export default {
  props: { version: { type: Object, required: true } },
  data () { return { report: null, loading: false, failed: false, disposed: false } },
  computed: {
    reference () {
      const reference = this.version.config?.backtest_reference
      return matches(runId, this.version.id) && matches(runId, reference?.run_id) && matches(sha256, reference.result_sha256) ? reference : null
    },
    unlinkedMessage () {
      return this.version.config?.backtest_reference ? 'strategyBacktestUnavailable' : 'strategyBacktestAbsent'
    },
    sourceUrl () {
      const url = this.report?.evidence.source_url
      return url === 'https://www.baostock.com/mainContent?file=history_K_data.md' ? url : ''
    },
    reasonLabel () {
      const labels = {
        bullish_trend_holdout_gate_met: 'strategyBacktestBuyReason',
        bearish_trend_settled_lot_available: 'strategyBacktestSellReason'
      }
      return labels[this.report?.reason_code]
    }
  },
  beforeDestroy () { this.disposed = true },
  methods: {
    t (key, params) { return this.$t('ashare.' + key, params) },
    money (value) { return new Intl.NumberFormat(this.$i18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value)) },
    percent (value) { return new Intl.NumberFormat(this.$i18n.locale, { style: 'percent', maximumFractionDigits: 6 }).format(Number(value)) },
    async loadReport () {
      if (!this.reference || this.loading || this.disposed) return
      this.loading = true
      this.failed = false
      this.report = null
      const { version, reference } = this
      try {
        const result = await paperRequest(`/strategy-versions/${version.id}/backtest`)
        if (this.disposed) return
        if (verifiedReport(result, reference)) this.report = result
        else this.failed = true
      } catch {
        if (!this.disposed) this.failed = true
      } finally {
        if (!this.disposed) this.loading = false
      }
    }
  }
}
</script>

<style scoped>
.strategy-backtest { border-top: 1px solid var(--qd-border, #d9d9d9); margin: 16px 0; padding-top: 12px; }
.strategy-backtest h4 { color: var(--qd-text); }
.strategy-backtest .ant-btn, .strategy-backtest .ant-alert, .strategy-backtest details { margin-bottom: 12px; }
.strategy-backtest dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 12px; }
.strategy-backtest dd { margin: 0; }
.strategy-backtest dd, .strategy-backtest li { overflow-wrap: anywhere; }
.strategy-backtest li { margin-top: 6px; }
</style>
