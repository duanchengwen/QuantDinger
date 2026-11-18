<template>
  <section class="strategy-panel">
    <p>{{ t('strategyVersionHelp') }}</p>
    <a-alert v-if="error" type="error" :message="error" />
    <a-button :disabled="busy" :loading="loading" @click="refresh">{{ t('refresh') }}</a-button>
    <a-form v-if="canWrite" layout="vertical" class="strategy-form" @submit.prevent="create">
      <a-form-item :label="t('symbol')">
        <a-select v-model="draft.symbol" :disabled="busy" :placeholder="t('symbol')">
          <a-select-option v-for="symbol in symbols" :key="symbol" :value="symbol">{{ symbol }}</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item :label="t('quantity')">
        <a-input-number v-model="draft.quantity" :min="100" :max="1000000" :step="100" :disabled="busy" />
      </a-form-item>
      <a-form-item :label="t('strategyThreshold')">
        <a-input-number v-model="draft.threshold" :min="0.01" :precision="2" :disabled="busy" />
      </a-form-item>
      <a-form-item :label="t('price')">
        <a-input-number v-model="draft.limit_price" :min="0.01" :precision="2" :disabled="busy" />
      </a-form-item>
      <a-form-item :label="t('strategyMaxCash')">
        <a-input-number v-model="draft.max_cash" :min="0.01" :precision="2" :disabled="busy" />
      </a-form-item>
      <div class="strategy-action">
        <a-button
          type="primary"
          html-type="submit"
          :disabled="!canSave"
          :loading="busy">{{ t('strategySaveDraft') }}</a-button>
      </div>
    </a-form>
    <a-form layout="vertical">
      <a-form-item :label="t('strategyVersions')">
        <a-select v-model="selectedId" :disabled="loading || busy" :placeholder="t('noData')">
          <a-select-option v-for="version in versions" :key="version.id" :value="version.id">
            {{ version.config.symbol }} · {{ t('strategyStatus.' + version.status) }} · {{ formatTime(version.created_at) }}
          </a-select-option>
        </a-select>
      </a-form-item>
    </a-form>
    <article v-if="selected" class="strategy-detail">
      <dl>
        <dt>{{ t('strategyVersionId') }}</dt><dd><code>{{ selected.id }}</code></dd>
        <dt>SHA-256</dt><dd><code>{{ selected.config_sha256 }}</code></dd>
        <dt>{{ t('symbol') }}</dt><dd>{{ selected.config.symbol }}</dd>
        <dt>{{ t('quantity') }}</dt><dd>{{ selected.config.quantity }}</dd>
        <dt>{{ t('strategyThreshold') }}</dt><dd>{{ selected.config.threshold }}</dd>
        <dt>{{ t('price') }}</dt><dd>{{ selected.config.limit_price }}</dd>
        <dt>{{ t('strategyMaxCash') }}</dt><dd>{{ selected.config.max_cash }}</dd>
        <dt>{{ t('status') }}</dt><dd>{{ t('strategyStatus.' + selected.status) }}</dd>
        <template v-if="selected.approved_at">
          <dt>{{ t('strategyApprovedAt') }}</dt><dd>{{ formatTime(selected.approved_at) }}</dd>
        </template>
        <template v-if="selected.revoked_at">
          <dt>{{ t('strategyRevokedAt') }}</dt><dd>{{ formatTime(selected.revoked_at) }}</dd>
        </template>
      </dl>
      <a-button
        v-if="canWrite && selected.status === 'draft'"
        type="primary"
        :disabled="loading || busy"
        @click="confirmDecision(selected, 'approved')">{{ t('strategyApprove') }}</a-button>
      <a-button
        v-if="canWrite && selected.status === 'approved'"
        type="danger"
        :disabled="loading || busy"
        @click="confirmDecision(selected, 'revoked')">{{ t('strategyRevoke') }}</a-button>
    </article>
  </section>
</template>

<script>
import { paperRequest } from '@/api/asharePaper'

const versionPath = '/strategy-versions'
const newKey = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('')

export default {
  props: {
    active: { type: Boolean, required: true },
    canWrite: { type: Boolean, required: true },
    symbols: { type: Array, required: true }
  },
  data () {
    return {
      versions: [],
      selectedId: '',
      loading: false,
      busy: false,
      error: '',
      request: null,
      draft: { symbol: undefined, quantity: 100, threshold: null, limit_price: null, max_cash: null }
    }
  },
  computed: {
    selected () { return this.versions.find(item => item.id === this.selectedId) },
    canSave () {
      const { symbol, quantity, threshold, limit_price: price, max_cash: cash } = this.draft
      return this.canWrite && !this.loading && !this.busy && this.symbols.includes(symbol) &&
        Number.isInteger(quantity) && quantity >= 100 && quantity % 100 === 0 &&
        Number(threshold) > 0 && Number(price) > 0 && Number(cash) > quantity * Number(price)
    }
  },
  watch: {
    active: {
      immediate: true,
      handler (open) { if (open) this.refresh() }
    }
  },
  methods: {
    t (key) { return this.$t('ashare.' + key) },
    formatTime (value) {
      return new Date(value).toLocaleString(this.$i18n.locale, { timeZone: 'Asia/Shanghai', hour12: false })
    },
    errorText (error) {
      const keys = {
        strategy_version_hash_mismatch: 'strategyVersionChanged',
        another_strategy_version_approved: 'strategyAnotherApproved',
        invalid_strategy_state_transition: 'strategyVersionChanged',
        strategy_symbol_not_watched: 'strategySymbolNotWatched'
      }
      const code = (error.response && error.response.data && error.response.data.msg) || error.message
      return this.t(keys[code] || 'strategyOperationFailed')
    },
    async refresh () {
      if (this.loading || this.busy) return
      this.loading = true
      this.error = ''
      try {
        const { versions } = await paperRequest(versionPath)
        this.versions = versions
        if (!this.selected) this.selectedId = versions.length ? versions[0].id : ''
      } catch (error) {
        this.versions = []
        this.error = this.errorText(error)
      } finally {
        this.loading = false
      }
    },
    storeResult (version) {
      const index = this.versions.findIndex(item => item.id === version.id)
      if (index === -1) this.versions.unshift(version)
      else this.versions.splice(index, 1, version)
      this.selectedId = version.id
    },
    async create () {
      if (!this.canSave) return
      this.busy = true
      this.error = ''
      try {
        const config = {
          mode: 'paper',
          kind: 'buy_once_below',
          symbol: this.draft.symbol,
          quantity: this.draft.quantity,
          threshold: String(this.draft.threshold),
          limit_price: String(this.draft.limit_price),
          max_cash: String(this.draft.max_cash)
        }
        const fingerprint = JSON.stringify(config)
        if (!this.request || this.request.fingerprint !== fingerprint) this.request = { fingerprint, key: newKey() }
        const result = await paperRequest(versionPath, 'post', { config, request_key: this.request.key })
        this.request = null
        this.storeResult(result)
      } catch (error) {
        this.error = this.errorText(error)
      } finally {
        this.busy = false
      }
    },
    confirmDecision (version, decision) {
      if (!this.canWrite || this.loading || this.busy) return
      this.$confirm({
        title: this.t(decision === 'approved' ? 'strategyApprove' : 'strategyRevoke'),
        content: `${version.id} · ${version.config_sha256}`,
        onOk: () => this.decide(version, decision)
      })
    },
    async decide (version, decision) {
      if (!this.canWrite || this.loading || this.busy) return
      this.busy = true
      this.error = ''
      try {
        const result = await paperRequest(`${versionPath}/${version.id}/decision`, 'post', {
          config_sha256: version.config_sha256, decision, confirmed: true
        })
        this.storeResult(result)
      } catch (error) {
        this.error = this.errorText(error)
      } finally {
        this.busy = false
      }
    }
  }
}
</script>

<style scoped>
.strategy-panel { color: var(--qd-text); }
.strategy-panel p, .strategy-detail dt, .strategy-detail dd { color: var(--qd-text); }
.strategy-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0; }
.strategy-form .ant-input-number { width: 100%; }
.strategy-action { align-self: end; margin-bottom: 24px; }
.strategy-detail dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 14px; }
.strategy-detail dd { margin: 0; overflow-wrap: anywhere; }
</style>
