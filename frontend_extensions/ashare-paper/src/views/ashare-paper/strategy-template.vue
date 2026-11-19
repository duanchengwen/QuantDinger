<template>
  <section class="strategy-template" aria-labelledby="strategy-template-heading">
    <header class="strategy-template-heading">
      <h3 id="strategy-template-heading">{{ t('strategyTemplateTitle') }}</h3>
      <a-tag v-if="loadState === 'ready'" :color="savedTemplate && savedTemplate.enabled ? 'green' : undefined">
        {{ t(savedTemplate && savedTemplate.enabled ? 'strategyTemplateEnabled' : 'strategyTemplateDisabled') }}
      </a-tag>
    </header>
    <p>{{ t('strategyTemplateHelp') }}</p>
    <a-alert v-if="!accountReady" type="info" show-icon :message="t('strategyTemplateAccount')" />
    <a-alert v-if="error" :type="conflict ? 'warning' : 'error'" show-icon :message="error" role="alert" />
    <a-alert v-if="saved" type="success" show-icon :message="t('strategyTemplateSaved')" role="status" />
    <div class="strategy-template-actions">
      <a-button
        v-if="accountReady"
        :disabled="saving"
        :loading="loadState === 'loading'"
        @click="refresh">{{ t('strategyTemplateReload') }}</a-button>
    </div>
    <a-form v-if="loadState === 'ready'" layout="vertical" @submit.prevent="save">
      <p v-if="!canWrite" class="strategy-template-read-only">{{ t('strategyTemplateReadOnly') }}</p>
      <a-form-item>
        <label for="strategy-template-enabled">{{ t('strategyTemplateAllowProposals') }}</label>
        <a-switch id="strategy-template-enabled" v-model="draft.enabled" :aria-label="t('strategyTemplateAllowProposals')" :disabled="!canEdit" />
      </a-form-item>
      <a-form-item :label="t('strategyTemplateScope')">
        <a-select v-model="draft.symbol_scope" :disabled="!canEdit">
          <a-select-option value="watched">{{ t('strategyTemplateWatched') }}</a-select-option>
          <a-select-option value="selected">{{ t('strategyTemplateSelected') }}</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item v-if="draft.symbol_scope === 'selected'" :label="t('strategyTemplateSelectedSymbols')">
        <a-select
          v-model="draft.symbols"
          mode="multiple"
          option-filter-prop="children"
          :options="symbolOptions"
          :placeholder="t('strategyTemplateChooseSymbols')"
          :disabled="!canEdit" />
        <small v-if="removedSymbols.length" class="strategy-template-note">{{ t('strategyTemplateUnwatched') }}</small>
      </a-form-item>
      <p v-if="draft.symbol_scope === 'watched' && !symbols.length" class="strategy-template-note">{{ t('strategyTemplateEmptyWatchlist') }}</p>
      <a-form-item :label="t('strategyTemplateCashCap')" :extra="t('strategyTemplateCashHelp')" html-for="strategy-template-cap">
        <a-input id="strategy-template-cap" v-model="draft.max_cash" inputmode="decimal" :disabled="!canEdit" />
      </a-form-item>
      <p v-if="canEdit && validationError" class="strategy-template-note">{{ t(validationError) }}</p>
      <a-button v-if="canWrite" type="primary" html-type="submit" :loading="saving" :disabled="!canSave">
        {{ t('strategyTemplateSave') }}
      </a-button>
    </a-form>
  </section>
</template>

<script>
import { paperRequest } from '@/api/asharePaper'

const templatePath = '/strategy-template'
const emptyTemplate = () => ({ enabled: false, symbol_scope: 'watched', symbols: [], max_cash: '' })
const scopes = ['watched', 'selected']

function validCash (value) {
  return typeof value === 'string' && /^\d{1,9}(?:\.\d{1,2})?$/.test(value) && Number(value) > 0
}

function readTemplate (response, previousRevision) {
  if (!response || !Number.isSafeInteger(response.revision) || response.revision < 0) throw new Error('invalid_template_response')
  const template = response.template
  if (template === null && response.revision === 0 && previousRevision === undefined) return { revision: response.revision, template }
  if ((previousRevision !== undefined && response.revision <= previousRevision) || !template ||
      typeof template.enabled !== 'boolean' || !scopes.includes(template.symbol_scope) ||
      !Array.isArray(template.symbols) || template.symbols.some(symbol => typeof symbol !== 'string') ||
      !validCash(template.max_cash)) throw new Error('invalid_template_response')
  const { enabled, symbol_scope: scope, symbols, max_cash: cap } = template
  return { revision: response.revision, template: { enabled, symbol_scope: scope, symbols, max_cash: cap } }
}

function errorCode (error) {
  return (error.response && error.response.data && error.response.data.msg) || error.message
}

export default {
  props: {
    active: { type: Boolean, required: true },
    accountReady: { type: Boolean, required: true },
    canWrite: { type: Boolean, required: true },
    symbols: { type: Array, required: true },
    symbolLabel: { type: Function, required: true }
  },
  data () {
    return {
      revision: null,
      draft: emptyTemplate(),
      savedTemplate: null,
      loadState: 'idle',
      requestId: 0,
      saving: false,
      conflict: false,
      error: '',
      saved: false
    }
  },
  computed: {
    removedSymbols () { return this.draft.symbols.filter(symbol => !this.symbols.includes(symbol)) },
    symbolOptions () {
      return [...this.symbols, ...this.removedSymbols].map(symbol => ({
        value: symbol,
        label: this.removedSymbols.includes(symbol)
          ? this.t('strategyTemplateRemovedSymbol', { symbol: this.symbolLabel(symbol) })
          : this.symbolLabel(symbol)
      }))
    },
    canEdit () { return this.active && this.accountReady && this.canWrite && this.loadState === 'ready' && !this.saving && !this.conflict },
    payload () {
      const { enabled, symbol_scope: scope, symbols, max_cash: cap } = this.draft
      return {
        enabled,
        symbol_scope: scope,
        symbols: scope === 'selected' ? [...symbols] : [],
        max_cash: cap.trim()
      }
    },
    validationError () {
      if (this.payload.symbol_scope === 'selected' && !this.payload.symbols.length) return 'strategyTemplateChooseSymbols'
      if (this.payload.symbols.length > 30) return 'strategyTemplateTooManySymbols'
      if (this.payload.enabled && this.payload.symbol_scope === 'selected' && this.removedSymbols.length) return 'strategyTemplateSymbolsNotWatched'
      if (!validCash(this.payload.max_cash)) return 'strategyTemplateMoneyInvalid'
      return ''
    },
    canSave () { return this.canEdit && !this.validationError }
  },
  watch: {
    active: { immediate: true, handler () { this.syncPresence() } },
    accountReady () { this.syncPresence() },
    draft: { deep: true, handler () { this.saved = false } }
  },
  beforeDestroy () { this.requestId += 1 },
  methods: {
    t (key, params) { return this.$t('ashare.' + key, params) },
    syncPresence () {
      if (this.active && this.accountReady) return this.refresh()
      this.requestId += 1
      this.loadState = 'idle'
      this.saving = false
      this.error = ''
      this.saved = false
    },
    isCurrent (id) { return this.active && this.accountReady && this.requestId === id },
    applyTemplate ({ revision, template }) {
      this.revision = revision
      this.savedTemplate = template
      this.draft = template ? { ...template, symbols: [...template.symbols] } : emptyTemplate()
      this.conflict = false
    },
    errorText (error, fallback) {
      if (error.response && error.response.status === 401) return this.t('strategyTemplateUnauthorized')
      if (error.response && error.response.status === 403) return this.t('strategyTemplateForbidden')
      const keys = {
        strategy_template_conflict: 'strategyTemplateConflict',
        strategy_template_symbols_not_watched: 'strategyTemplateSymbolsNotWatched',
        invalid_strategy_money: 'strategyTemplateMoneyInvalid',
        paper_account_required: 'strategyTemplateAccount'
      }
      return this.t(keys[errorCode(error)] || fallback)
    },
    async refresh () {
      if (!this.active || !this.accountReady || this.loadState === 'loading' || this.saving) return
      const id = ++this.requestId
      this.loadState = 'loading'
      this.error = ''
      this.saved = false
      try {
        const response = await paperRequest(templatePath)
        if (!this.isCurrent(id)) return
        this.applyTemplate(readTemplate(response))
        this.loadState = 'ready'
      } catch (error) {
        if (!this.isCurrent(id)) return
        this.loadState = 'error'
        this.error = this.errorText(error, 'strategyTemplateLoadFailed')
      }
    },
    async save () {
      if (!this.canSave) return
      const id = ++this.requestId
      const revision = this.revision
      this.saving = true
      this.error = ''
      this.saved = false
      try {
        const response = await paperRequest(templatePath, 'put', { expected_revision: revision, template: this.payload })
        if (!this.isCurrent(id)) return
        this.applyTemplate(readTemplate(response, revision))
        this.$nextTick(() => { if (this.isCurrent(id)) this.saved = true })
      } catch (error) {
        if (!this.isCurrent(id)) return
        this.conflict = errorCode(error) === 'strategy_template_conflict'
        this.error = this.errorText(error, 'strategyTemplateSaveFailed')
      } finally {
        if (this.isCurrent(id)) this.saving = false
      }
    }
  }
}
</script>

<style scoped>
.strategy-template { border-bottom: 1px solid var(--qd-border, #d9d9d9); margin-bottom: 20px; padding-bottom: 20px; }
.strategy-template-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.strategy-template-heading h3 { color: var(--qd-text); margin: 0; }
.strategy-template-actions, .strategy-template .ant-alert { margin: 0 0 12px; }
.strategy-template-actions:empty { display: none; }
.strategy-template .ant-switch { margin-left: 8px; }
.strategy-template .ant-input { max-width: 240px; }
.strategy-template-note { display: block; margin: 8px 0; }
</style>
