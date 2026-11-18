<template>
  <main class="ashare-paper qd-workspace-page" :class="{ 'theme-dark': isDarkTheme }">
    <header class="paper-heading">
      <div><a-tag color="cyan">{{ t('paper') }}</a-tag><h1>{{ t('title') }}</h1><p>{{ t('subtitle') }}</p></div>
      <div class="paper-health"><a-tag :color="workerFresh ? 'green' : 'orange'">{{ t('worker') }}: {{ workerLabel }}</a-tag><br><small>{{ t('updated') }} {{ formatTime(updatedAt) }}</small><a-button :loading="loading" @click="refresh">{{ t('refresh') }}</a-button><a-button @click="showFeishu">{{ t('feishu') }}</a-button></div>
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
      <div slot="extra"><small>{{ t('quoteHint') }}</small> <a-button :disabled="readOnly || !state.account" :loading="suggestionsLoading" @click="openBatchReview">{{ t('batchReview') }}</a-button></div>
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
          <a-form-item :label="t('symbol')"><a-select v-model="order.symbol" @change="orderChanged"><a-select-option v-for="symbol in symbols" :key="symbol" :value="symbol">{{ symbolLabel(symbol) }}</a-select-option></a-select></a-form-item>
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
        <a-table :columns="positionColumns" :data-source="namedPositions" :row-key="row => row.symbol + row.acquired_day" size="small" :pagination="{ pageSize: 8 }">
          <template slot="emptyText">{{ t('noData') }}</template>
        </a-table>
      </a-card>
    </div>

    <a-card class="paper-section">
      <p v-if="historyTruncated" class="paper-muted">{{ t('historyBounded') }}</p>
      <a-tabs ref="detailTabs" v-model="activeTab" @change="onTabChange">
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
            :scroll="{ x: 950 }">
            <template slot="money" slot-scope="value">{{ value == null ? '—' : money(value) }}</template>
            <template slot="reconciliation" slot-scope="value"><a-tag :color="value === true ? 'green' : 'red'">{{ value === true ? t('reconciled') : t('reconciliationFailed') }}</a-tag></template>
            <template slot="reportFeishu" slot-scope="value">{{ reportFeishuLabel(value) }}</template>
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
        <a-tab-pane key="universe" :tab="t('universe')">
          <p class="paper-muted">{{ t('universeHelp') }}</p>
          <a-form layout="inline" @submit.prevent="queryUniverse"><a-form-item :label="t('tradingDay')"><a-input v-model="universeDay" placeholder="YYYY-MM-DD" /></a-form-item><a-form-item><a-button html-type="submit" :loading="universeLoading">{{ t('queryUniverse') }}</a-button></a-form-item></a-form>
          <a-alert v-if="universeError" type="error" :message="universeError" class="paper-alert" />
          <a-alert v-if="universeResult && universeResult.status === 'not_observed'" type="warning" :message="t('universeMissing')" class="paper-alert" />
          <p v-else-if="universeResult" class="paper-muted">{{ t('universeCount', { count: universeResult.total || 0 }) }} · {{ universeResult.trading_day }} · {{ universeResult.source }}<br>{{ t('received') }} {{ formatTime(universeResult.received_at) }}<br>{{ t('evidenceHash') }}: {{ universeResult.source_hash }}</p>
          <a-table :columns="universeColumns" :data-source="universeRows" row-key="symbol" size="small" :pagination="false">
            <template slot="status" slot-scope="value">{{ value == null ? t('unknown') : value === true || value === '1' ? t('yes') : t('no') }}</template>
            <template slot="emptyText">{{ t('universeMissing') }}</template>
          </a-table>
          <a-button :disabled="universeOffset === 0 || universeLoading" @click="pageUniverse(-100)">{{ t('previous') }}</a-button> <a-button :disabled="universeRows.length < 100 || universeLoading" @click="pageUniverse(100)">{{ t('next') }}</a-button>
        </a-tab-pane>
        <a-tab-pane key="themes" :tab="t('themeMembers')">
          <p class="paper-muted">{{ t('themeHelp') }}</p>
          <div class="paper-theme-controls">
            <a-select
              :value="themeView.selectedBoard || undefined"
              :placeholder="t('themeSelect')"
              :disabled="themeView.catalogLoading || !themeView.themes.length"
              :aria-label="t('themeSelect')"
              @change="selectTheme">
              <a-select-option v-for="theme in themeView.themes" :key="theme.board_code" :value="theme.board_code">{{ theme.board_name }} · {{ theme.board_code }}</a-select-option>
            </a-select>
            <a-button :loading="themeView.catalogLoading" @click="refreshThemes">{{ t('themeRefresh') }}</a-button>
          </div>
          <a-alert v-if="themeView.catalogError || themeView.snapshotError" type="error" show-icon :message="themeView.catalogError || themeView.snapshotError" class="paper-alert" />
          <a-alert v-else-if="themeView.catalogStatus === 'not_observed' || (themeView.snapshot && themeView.snapshot.status === 'not_observed')" type="warning" show-icon :message="t('themeNotObserved')" class="paper-alert" />
          <section v-if="themeView.snapshot && themeView.snapshot.status === 'observed'" class="paper-theme-evidence">
            <h3>{{ themeView.snapshot.board_name }} · {{ themeView.snapshot.board_code }} <a-tag>{{ t('themeArchived') }}</a-tag></h3>
            <p>{{ t('received') }}: {{ formatTime(themeView.snapshot.received_at) }}</p>
            <p>{{ t('themeCounts', { supported: themeView.snapshot.member_count, provider: themeView.snapshot.provider_total, excluded: themeView.snapshot.excluded_count }) }}</p>
            <p>{{ t('source') }}: {{ themeView.snapshot.source }}<br>{{ t('evidenceHash') }}: <code>{{ themeView.snapshot.source_hash }}</code></p>
          </section>
          <a-table
            v-if="themeView.snapshot && themeView.snapshot.status === 'observed'"
            :columns="themeColumns"
            :data-source="themeRows"
            row-key="symbol"
            size="small"
            :pagination="false"
            :loading="themeView.snapshotLoading">
            <template slot="emptyText">{{ t('themeEmptyPage') }}</template>
          </a-table>
          <p v-if="themeView.snapshotLoading" class="paper-muted" role="status">{{ t('themeLoading') }}</p>
          <div v-if="themeView.snapshot && themeView.snapshot.status === 'observed'" class="paper-theme-pagination">
            <span>{{ t('themePageRange', themeRange) }}</span>
            <a-button :disabled="themeView.offset === 0 || themeView.snapshotLoading || themeView.catalogLoading" @click="pageTheme(-1)">{{ t('previous') }}</a-button>
            <a-button :disabled="!themeNext || themeView.snapshotLoading || themeView.catalogLoading" @click="pageTheme(1)">{{ t('next') }}</a-button>
          </div>
        </a-tab-pane>
        <a-tab-pane key="research" :tab="t('researchTab')">
          <a-alert type="info" show-icon :message="t('researchOnly')" :description="t('researchHelp')" class="paper-alert" />
          <p class="paper-muted">{{ t('researchSavedTopics') }}: {{ savedResearchTopics || t('researchNoTopics') }}</p>
          <div class="paper-theme-controls">
            <a-button :loading="researchView.loading" @click="refreshResearch">{{ t('researchRefresh') }}</a-button>
            <a-button type="primary" :loading="researchView.submitting" :disabled="readOnly || !state.account || !researchReady" @click="generateResearch">{{ t(researchView.retryPending ? 'researchRetry' : 'researchGenerate') }}</a-button>
            <small>{{ t('researchCost') }}</small>
          </div>
          <a-alert v-if="researchView.error || researchView.submitError" type="error" show-icon :message="researchView.error || researchView.submitError" class="paper-alert" />
          <a-alert v-if="researchView.status && !researchView.status.model_configured && researchView.status.configuration_error !== 'model_not_supported' && researchView.status.blocked_reason !== 'model_not_supported'" type="warning" show-icon :message="t('researchState.needs_model_configuration')" class="paper-alert" />
          <a-alert v-if="researchView.status && (researchView.status.configuration_error === 'model_not_supported' || researchView.status.blocked_reason === 'model_not_supported')" type="warning" show-icon :message="t('researchModelUnsupported')" class="paper-alert" />
          <p v-if="researchView.pollExhausted" role="status" class="paper-muted">{{ t('researchPollStopped') }}</p>
          <div class="paper-research-grid">
            <section class="paper-research-news">
              <h3>{{ t('researchNews') }}</h3>
              <p class="paper-muted">{{ t('researchNewsCoverage') }}</p>
              <p v-if="!researchView.loading && !researchView.error && !researchView.news.length" class="paper-muted">{{ t('researchNoNews') }}</p>
              <article v-for="item in researchView.news" :key="item.id" class="paper-research-item">
                <h4><a v-if="newsUrl(item.source_url)" :href="newsUrl(item.source_url)" target="_blank" rel="noopener noreferrer">{{ item.title }}</a><span v-else>{{ item.title }}</span></h4>
                <p>{{ item.source || t('unknown') }} · {{ newsType(item.content_kind) }}</p>
                <p>{{ t('researchPublished') }}: {{ formatTime(item.published_at) }}<br>{{ t('received') }}: {{ formatTime(item.received_at) }}</p>
                <small>{{ t('researchNewsId') }}: {{ item.id }}<br>{{ t('researchSourceHash') }}: <code>{{ item.source_hash }}</code><template v-if="item.pdf_hash"><br>{{ t('researchPdfHash') }}: <code>{{ item.pdf_hash }}</code></template></small>
              </article>
            </section>
            <section class="paper-research-results">
              <h3>{{ t('researchRuns') }}</h3>
              <a-select
                :value="researchView.selectedId || undefined"
                :placeholder="t('researchSelect')"
                :aria-label="t('researchSelect')"
                :disabled="researchView.loading || !researchView.runs.length"
                class="paper-research-select"
                @change="selectResearch">
                <a-select-option v-for="run in researchView.runs" :key="run.id" :value="run.id">{{ formatTime(run.created_at) }} · {{ t('researchState.' + run.status) }}</a-select-option>
              </a-select>
              <p v-if="!researchView.loading && !researchView.error && !researchView.runs.length" class="paper-muted">{{ t('researchNoRuns') }}</p>
              <p v-if="researchView.loading || researchView.detailLoading" class="paper-muted" role="status">{{ t('researchLoading') }}</p>
              <a-alert v-if="researchView.detailError" type="error" :message="researchView.detailError" class="paper-alert" />
              <article v-if="researchView.detail" class="paper-research-detail">
                <a-tag color="orange">{{ t('researchOnly') }}</a-tag>
                <p role="status">{{ t('researchState.' + researchView.detail.status) }}</p>
                <p v-if="researchView.detail.error_code" class="paper-muted">{{ researchReason(researchView.detail.error_code) }}</p>
                <p v-if="researchView.detail.status === 'failed'" class="paper-muted">{{ t('researchRunFailed') }}</p>
                <template v-if="researchView.detail.status === 'completed'">
                  <p>{{ researchView.detail.summary }}</p>
                  <p v-if="!researchView.detail.candidates.length" class="paper-muted">{{ t('researchNoCandidates') }}</p>
                  <section v-for="candidate in researchView.detail.candidates" :key="candidate.symbol" class="paper-research-item">
                    <h4>{{ candidate.symbol }} · {{ candidate.name }}</h4>
                    <p>{{ candidate.rationale }}</p>
                    <p class="paper-muted">{{ researchEvidence(candidate.evidence_scope) }}</p>
                    <p>{{ t('researchEvidenceIds') }}: {{ (candidate.evidence_news_ids || []).join(', ') || '—' }}</p>
                    <strong>{{ t('researchRisks') }}</strong>
                    <ul><li v-for="(risk, index) in candidate.risks" :key="index">{{ risk }}</li></ul>
                    <a-button
                      v-if="!readOnly && state.account"
                      size="small"
                      :disabled="!symbols.includes(candidate.symbol)"
                      @click="startResearchDraft(candidate.symbol)">{{ t('strategyStartResearchDraft') }}</a-button>
                    <p v-if="!readOnly && state.account && !symbols.includes(candidate.symbol)" class="paper-muted">{{ t('strategySymbolNotWatched') }}</p>
                  </section>
                  <template v-if="researchView.detail.risks.length"><h4>{{ t('researchRisks') }}</h4><ul><li v-for="(risk, index) in researchView.detail.risks" :key="index">{{ risk }}</li></ul></template>
                </template>
                <p class="paper-muted">{{ t('researchRunId') }}: {{ researchView.detail.id }}<br>{{ t('researchCreated') }}: {{ formatTime(researchView.detail.created_at) }}<br>{{ t('researchCompleted') }}: {{ formatTime(researchView.detail.completed_at) }}</p>
                <small>{{ t('researchInputHash') }}: <code>{{ researchView.detail.input_hash || '—' }}</code><br>{{ t('researchOutputHash') }}: <code>{{ researchView.detail.output_hash || '—' }}</code></small>
              </article>
            </section>
          </div>
        </a-tab-pane>
        <a-tab-pane key="strategy-versions" :tab="t('strategyVersions')">
          <strategy-versions
            :active="activeTab === 'strategy-versions'"
            :can-write="!readOnly && Boolean(state.account)"
            :symbols="symbols"
            :research-seed="strategyResearchSeed" />
        </a-tab-pane>
        <a-tab-pane key="settings" :tab="t('settings')">
          <h3>{{ t('researchPreferences') }}</h3>
          <p class="paper-muted">{{ t('researchPreferencesHelp') }}</p>
          <a-checkbox-group v-model="researchPreferences.topics" :disabled="readOnly"><a-checkbox v-for="topic in ['ai', 'robotics', 'new_energy', 'semiconductors']" :key="topic" :value="topic">{{ t('topic.' + topic) }}</a-checkbox></a-checkbox-group>
          <p><a-checkbox v-model="researchPreferences.follow_current_events" :disabled="readOnly">{{ t('followCurrentEvents') }}</a-checkbox></p>
          <a-form-item :label="t('customTopics')"><a-input v-model="researchPreferences.custom_topics" :max-length="240" :disabled="readOnly" /></a-form-item>
          <ul class="paper-saved-symbols"><li v-for="symbol in symbols" :key="symbol">{{ symbolLabel(symbol) }}</li></ul>
          <a-form layout="vertical" @submit.prevent="saveSettings"><a-form-item :label="t('symbols')"><a-input v-model="settingsSymbols" placeholder="sz.000001,sh.600000" /></a-form-item><a-form-item :label="t('interval')"><a-input-number v-model="settingsInterval" :min="2" :max="3600" /></a-form-item><a-button html-type="submit" :disabled="readOnly || !state.account" :loading="busy">{{ t('save') }}</a-button></a-form>
        </a-tab-pane>
        <a-tab-pane key="feishu" :tab="t('feishu')">
          <div ref="feishuSettings" class="paper-bot"><h3>{{ t('feishu') }} <a-tag :color="botConfigured ? 'green' : undefined">{{ botConfigured ? t('configured') : t('notConfigured') }}</a-tag><a-tag v-if="state.notifications && state.notifications.signing_enabled">{{ t('signingOn') }}</a-tag></h3>
            <p>{{ t('feishuHint') }}</p><ol class="paper-steps"><li>{{ t('feishuStep1') }}</li><li>{{ t('feishuStep2') }}</li><li>{{ t('feishuStep3') }}</li></ol>
            <p><a href="https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot" target="_blank" rel="noopener noreferrer">{{ t('feishuGuide') }}</a></p>
            <a-form layout="vertical" @submit.prevent="saveBot"><a-form-item :label="t('webhook')"><a-input-password v-model="bot.webhook_url" autocomplete="new-password" /></a-form-item><a-form-item :label="t('signing')"><a-input-password v-model="bot.signing_secret" autocomplete="new-password" /></a-form-item><p class="paper-muted">{{ t('secretHelp') }}</p><a-button html-type="submit" :disabled="readOnly || !bot.webhook_url || !state.account" :loading="busy">{{ t('saveBot') }}</a-button> <a-button :disabled="readOnly || !botConfigured" :loading="busy" @click="testBot">{{ t('testBot') }}</a-button> <a-button type="danger" :disabled="readOnly || !botConfigured || busy" @click="confirmDisableBot">{{ t('disableBot') }}</a-button></a-form>
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-card>

    <a-modal
      :visible="batchVisible"
      :title="t('batchReview')"
      :width="900"
      :confirm-loading="busy"
      :ok-button-props="{ props: { disabled: !batchCanSave } }"
      @ok="saveBatchReview"
      @cancel="batchVisible = false">
      <a-alert type="info" :message="t('batchHelp')" class="paper-alert" />
      <p>{{ t('tradingDay') }}: {{ batchDay }}</p>
      <div v-for="row in reviewRows" :key="row.symbol" class="paper-review-row">
        <strong>{{ row.symbol }}</strong> · {{ t('lower') }} {{ row.limit_down || '—' }} · {{ t('upper') }} {{ row.limit_up || '—' }}
        <p>{{ t('stStatus') }}: {{ statusLabel(row.st) }} · {{ t('suspensionStatus') }}: {{ statusLabel(row.suspended) }}</p>
        <p v-if="row.status_evidence" class="paper-muted">{{ t('source') }}: {{ row.status_evidence.source }} · {{ row.status_evidence.trading_day }} · {{ formatTime(row.status_evidence.available_at) }}<br>{{ t('evidenceHash') }}: {{ row.status_evidence.evidence_sha256 }}</p>
        <p v-else class="paper-muted">{{ t('currentStatusMissing') }}</p>
        <p v-if="row.quote_evidence" class="paper-muted">{{ t('quoteEvidence') }}: {{ row.quote_evidence.source || '—' }} · {{ formatTime(row.quote_evidence.exchange_time) }}<br>{{ t('evidenceHash') }}: {{ row.quote_evidence.evidence_sha256 }}</p>
        <a-alert v-if="row.blockers.length" type="warning" :message="t('candidateBlocked')" class="paper-alert" />
        <a-checkbox v-model="row.confirmed" :disabled="row.blockers.length > 0">{{ t('reviewCheck') }}</a-checkbox>
      </div>
      <a-form-item :label="t('batchEvidence')"><a-textarea v-model="batchEvidence" :rows="3" :max-length="1000" /></a-form-item>
    </a-modal>

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
import { createThemeState, createThemeBrowser, themeMembers, themeHasNext, themePageRange } from './theme-state.mjs'
import { createResearchState, createResearchBrowser, researchCanGenerate, safeNewsUrl, researchEvidenceKey, researchReasonKey } from './research-state.mjs'

const emptyState = () => ({ account: null, quotes: [], orders: [], positions: [], fills: [], rules: {}, settings: {}, worker: {}, notifications: {} })
export default {
  components: { StrategyVersions: () => import('./strategy-versions.vue') },
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
      researchPreferences: { topics: [], follow_current_events: false, custom_topics: '' },
settingsLoaded: false,
      order: { symbol: 'sz.000001', side: 'buy', quantity: 100, limit_price: null },
orderId: '',
      reviewVisible: false,
reviewChecked: false,
review: { symbol: '', trading_day: '', evidence: '', limit_down: null, limit_up: null },
      bot: { webhook_url: '', signing_secret: '' },
      activeTab: 'orders',
      strategyResearchSeed: null,
reports: [],
reportsLoading: false,
reportsError: '',
      suggestionsLoading: false,
      batchVisible: false,
      batchDay: '',
      reviewRows: [],
      batchEvidence: '',
      universeDay: shanghaiDate(Date.now()),
      universeOffset: 0,
      universeResult: null,
      universeLoading: false,
      universeError: '',
      themeView: createThemeState(),
      themeController: null,
      researchView: createResearchState(),
      researchController: null
}
  },
  computed: {
    researchReady () { return researchCanGenerate(this.researchView) },
    savedResearchTopics () { return ((this.state.settings.research_preferences || {}).topics || []).map(topic => this.t('topic.' + topic)).join(' / ') },
    themeRows () { return themeMembers(this.themeView) },
    themeNext () { return themeHasNext(this.themeView) },
    themeRange () { return themePageRange(this.themeView) },
    themeColumns () { return [{ title: this.t('symbol'), dataIndex: 'symbol' }, { title: this.t('name'), dataIndex: 'name' }] },
    batchCanSave () { return this.batchEvidence.trim().length >= 3 && this.reviewRows.some(row => row.confirmed && row.blockers.length === 0) },
    universeRows () { return this.universeResult ? (this.universeResult.members || []).map(row => ({ ...row, trading_day: this.universeResult.trading_day })) : [] },
    universeColumns () { return [{ title: this.t('symbol'), dataIndex: 'symbol' }, { title: this.t('name'), dataIndex: 'name' }, { title: this.t('tradingDay'), dataIndex: 'trading_day' }, { title: this.t('suspensionStatus'), dataIndex: 'suspended', scopedSlots: { customRender: 'status' } }] },
    isDarkTheme () { return ['dark', 'realdark'].includes(this.$store.getters.theme) },
    metrics () { return [{ key: 'cash', label: 'cash' }, { key: 'available_cash', label: 'available' }, { key: 'reserved_cash', label: 'reserved' }, { key: 'total_fees', label: 'fees' }] },
    symbols () { return this.state.settings.symbols || ['sz.000001'] },
    readOnly () { const info = this.$store.state.user.info || {}; return roleIsViewer(info.role) },
    workerFresh () { const worker = this.state.worker || {}; const age = this.age(worker.observed_at); return worker.state === 'healthy' && age >= 0 && age <= 30 },
    workerLabel () { return this.workerFresh ? this.t('state.healthy') : (this.state.worker.state === 'healthy' ? this.t('stale') : this.state.worker.state ? this.t('state.' + this.state.worker.state) : this.t('unknown')) },
    canOrder () { const q = this.state.quotes.find(q => q.symbol === this.order.symbol); return !this.readOnly && !!this.state.account && !this.error && this.workerFresh && this.quoteFresh(q) && this.ruleReady(this.order.symbol) && this.order.quantity > 0 && this.order.quantity % 100 === 0 && Number(this.order.limit_price) > 0 },
    botConfigured () { return !!(this.state.notifications && this.state.notifications.configured) },
    quoteColumns () { return [{ title: this.t('symbol'), dataIndex: 'symbol', scopedSlots: { customRender: 'symbol' } }, { title: this.t('last'), dataIndex: 'last' }, { title: this.t('bid'), key: 'bid', scopedSlots: { customRender: 'depthBid' } }, { title: this.t('ask'), key: 'ask', scopedSlots: { customRender: 'depthAsk' } }, { title: this.t('time'), key: 'time', scopedSlots: { customRender: 'time' } }, { title: this.t('status'), key: 'quality', scopedSlots: { customRender: 'quality' } }, { title: this.t('rules'), key: 'rules', scopedSlots: { customRender: 'rules' } }] },
    namedPositions () { return (this.state.positions || []).map(position => ({ ...position, name: this.symbolName(position.symbol) })) },
    positionColumns () { return this.columns(['symbol', 'name', 'quantity', 'acquired_day']) },
    orderColumns () { return [...this.columns(['id', 'symbol', 'side', 'quantity', 'filled', 'limit_price', 'status']), { title: this.t('action'), key: 'cancel', scopedSlots: { customRender: 'cancel' } }] },
    historyTruncated () { return Object.values((this.state.history || {}).truncated || {}).some(Boolean) },
    reportRows () { return this.reports.map(item => ({ ...item.report, trading_day: item.trading_day, generated_at: item.generated_at, feishu_delivery: item.feishu_delivery, reconciliation_ok: item.report.reconciliation && item.report.reconciliation.ok, observation: item.report.observation_summary && item.report.observation_summary.poll_count != null ? `${item.report.observation_summary.wall_clock_polls} / ${item.report.observation_summary.controlled_time_polls}` : '—' })) },
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
      { title: this.t('reportFeishu'), dataIndex: 'feishu_delivery', scopedSlots: { customRender: 'reportFeishu' } },
      { title: this.t('reportGenerated'), dataIndex: 'generated_at', scopedSlots: { customRender: 'time' } }
    ]
},
    fillColumns () { return this.columns(['order_id', 'symbol', 'side', 'quantity', 'price', 'fee', 'exchange_time']) }
  },
  created () { this.themeController = createThemeBrowser(this.themeView, paperRequest, error => this.themeErrorMessage(error)); this.researchController = createResearchBrowser(this.researchView, paperRequest, error => this.researchErrorMessage(error)) },
  mounted () { document.addEventListener('visibilitychange', this.syncResearchVisibility); this.clock = setInterval(() => { this.now = Date.now() }, 1000); this.poll() },
  beforeDestroy () { document.removeEventListener('visibilitychange', this.syncResearchVisibility); this.researchController.dispose(); this.themeController.dispose(); this.stopped = true; clearTimeout(this.timer); clearInterval(this.clock) },
  methods: {
    startResearchDraft (symbol) {
      const run = this.researchView.detail
      if (this.readOnly || !this.state.account || !this.symbols.includes(symbol) ||
          !run || run.status !== 'completed' || !run.candidates.some(item => item.symbol === symbol)) return
      this.strategyResearchSeed = {
        symbol, reference: { run_id: run.id, input_hash: run.input_hash, output_hash: run.output_hash }
      }
      this.activeTab = 'strategy-versions'
      this.syncResearchVisibility()
    },
    t (key, values) { return this.$t('ashare.' + key, values) },
    errorMessage (err) { const message = (err.response && err.response.data && err.response.data.msg) || err.message || this.t('error'); const keys = { auth_service_unavailable: 'service', paper_database_unavailable: 'service', paper_database_not_configured: 'service', paper_worker_stale: 'worker', paper_worker_not_healthy: 'worker', outside_continuous_auction: 'session', not_trading_day: 'session', fresh_quote_required: 'quote', 'stale quote': 'quote', 'insufficient available cash': 'cash', 'insufficient T+1 available shares': 'shares' }; return keys[message] ? this.t('error.' + keys[message]) : message },
    columns (keys) { const names = { acquired_day: 'acquired', limit_price: 'price', fee: 'fees', order_id: 'id', exchange_time: 'time' }; return keys.map(key => ({ title: this.t(names[key] || key), dataIndex: key, ...(['side', 'status'].includes(key) ? { scopedSlots: { customRender: key } } : {}), ...(key === 'exchange_time' ? { scopedSlots: { customRender: 'time' } } : {}) })) },
    money (value) { return Number(value || 0).toLocaleString(this.$i18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    formatTime (value) { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(this.$i18n.locale, { timeZone: 'Asia/Shanghai', hour12: false }) },
    age (value) { return ageSeconds(value, this.now) },
    quoteFresh (quote) { return quoteIsFresh(quote, this.now) },
    today () { return shanghaiDate(this.now) },
    ruleReady (symbol) { return ruleIsReady((this.state.rules || {})[symbol], this.now) },
    onTabChange (key) { this.researchController.setActive(key === 'research' && !document.hidden); if (key === 'reports') this.loadReports(); if (key === 'universe' && !this.universeResult) this.loadUniverse(); if (key === 'themes') this.themeController.ensureLoaded() },
    syncResearchVisibility () { this.researchController.setActive(this.activeTab === 'research' && !document.hidden) },
    refreshResearch () { return this.researchController.refresh() },
    selectResearch (id) { return this.researchController.select(id) },
    generateResearch () { if (!this.readOnly && this.state.account) return this.researchController.generate() },
    researchReason (code) { return this.t(researchReasonKey(code)) },
    researchEvidence (scope) { return this.t(researchEvidenceKey(scope)) },
    newsUrl (value) { return safeNewsUrl(value) },
    newsType (value) { return this.t('researchNewsType.' + (['news', 'announcement', 'announcement_title'].includes(value) ? value : 'other')) },
    researchErrorMessage (error) { const response = error.response || {}; if (response.status === 401) return this.t('researchSessionError'); return this.t('researchLoadFailed') },
    themeErrorMessage (error) { const response = error.response || {}; const message = (response.data && response.data.msg) || error.message || ''; if (response.status === 401) return this.t('themeSessionError'); if (/evidence|hash_mismatch/.test(message)) return this.t('themeEvidenceError'); if (response.status === 503 || /database_unavailable|database_not_configured|auth_service_unavailable/.test(message)) return this.t('error.service'); return this.t('themeLoadFailed') },
    refreshThemes () { return this.themeController.refresh() },
    selectTheme (board) { return this.themeController.select(board) },
    pageTheme (direction) { return this.themeController.page(direction) },
    showFeishu () { this.activeTab = 'feishu'; this.researchController.setActive(false); this.$nextTick(() => { const element = this.$refs.feishuSettings; if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' }) }) },
    symbolName (symbol) { const quote = (this.state.quotes || []).find(row => row.symbol === symbol); return quote && typeof quote.name === 'string' && quote.name.trim() ? quote.name.trim() : this.t('nameUnavailable') },
    symbolLabel (symbol) { return symbol + ' · ' + this.symbolName(symbol) },
    statusLabel (value) { return value == null ? this.t('unknownNeedsReview') : value ? this.t('yes') : this.t('no') },
    async openBatchReview () { if (this.suggestionsLoading) return; this.suggestionsLoading = true; try { const result = await paperRequest('/rules/suggestions'); this.batchDay = result.trading_day; this.reviewRows = result.rows.map(row => ({ ...row, confirmed: false })); this.batchEvidence = ''; this.batchVisible = true } catch (err) { this.error = this.errorMessage(err) } finally { this.suggestionsLoading = false } },
    async saveBatchReview () { if (!this.batchCanSave) return; const rules = this.reviewRows.filter(row => row.confirmed && row.blockers.length === 0).map(row => ({ symbol: row.symbol, trading_day: this.batchDay, suspended: false, st: false, limit_up: String(row.limit_up), limit_down: String(row.limit_down), evidence: this.batchEvidence.trim() })); try { await this.mutate('/rules/review-batch', 'post', { confirmed: true, rules }, this.t('saved')); this.batchVisible = false } catch (_) {} },
    async loadUniverse () { if (this.universeLoading) return; this.universeLoading = true; try { this.universeResult = await paperRequest('/universe?trading_day=' + encodeURIComponent(this.universeDay) + '&limit=100&offset=' + this.universeOffset); this.universeError = '' } catch (err) { this.universeResult = null; this.universeError = this.errorMessage(err) } finally { this.universeLoading = false } },
    queryUniverse () { this.universeOffset = 0; this.loadUniverse() },
    pageUniverse (offset) { this.universeOffset = Math.max(0, this.universeOffset + offset); this.loadUniverse() },
    reportFeishuLabel (value) {
      const known = ['unknown', 'webhook_acknowledged', 'disabled', 'rejected', 'failed']
      return this.t('reportFeishu.' + (value && known.includes(value.status) ? value.status : 'not_observed'))
    },
    async loadReports () { if (this.reportsLoading || this.stopped) return; this.reportsLoading = true; try { const result = await paperRequest('/daily-reports?limit=30'); if (!this.stopped) { this.reports = result; this.reportsError = '' } } catch (err) { this.reportsError = this.errorMessage(err) } finally { this.reportsLoading = false } },
    async refresh () { if (this.loading || this.stopped) return; this.loading = true; try { const data = await paperRequest(); if (this.stopped) return; this.now = Date.now(); this.state = { ...emptyState(), ...data }; this.error = ''; this.updatedAt = new Date().toISOString(); if (!this.settingsLoaded) { this.settingsSymbols = this.symbols.join(','); this.settingsInterval = this.state.settings.interval_seconds || 5; this.researchPreferences = { topics: [], follow_current_events: false, custom_topics: '', ...(this.state.settings.research_preferences || {}) }; this.order.symbol = this.symbols[0]; this.settingsLoaded = true } } catch (err) { this.error = this.errorMessage(err) } finally { this.loading = false } },
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
    async saveSettings () { try { await this.mutate('/settings', 'put', { symbols: this.settingsSymbols.split(',').map(s => s.trim()).filter(Boolean), interval_seconds: this.settingsInterval, research_preferences: this.researchPreferences }, this.t('saved')) } catch (_) {} },
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
.paper-review-row { padding: 12px 0; border-bottom: 1px solid var(--qd-border); margin-bottom: 12px; overflow-wrap: anywhere; }
.paper-theme-controls, .paper-theme-pagination { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 0 0 16px; }
.paper-theme-controls .ant-select { min-width: 240px; max-width: 100%; }
.paper-theme-evidence { margin: 16px 0; overflow-wrap: anywhere; }
.paper-theme-evidence h3 { color: var(--qd-text); }
.paper-theme-evidence p, .paper-theme-pagination { color: var(--qd-text-secondary); }
.paper-theme-pagination { margin-top: 16px; }
.paper-saved-symbols { padding-left: 20px; color: var(--qd-text-secondary); line-height: 1.8; }
.paper-research-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); gap: 28px; margin-top: 24px; }
.ashare-paper .paper-research-grid,
.ashare-paper .paper-research-grid p,
.ashare-paper .paper-research-grid h3,
.ashare-paper .paper-research-grid h4,
.ashare-paper .paper-research-grid ul,
.ashare-paper .paper-research-grid li,
.ashare-paper .paper-research-grid strong { color: var(--qd-text); }
.ashare-paper .paper-research-grid .paper-muted,
.ashare-paper .paper-research-grid small { color: var(--qd-text-secondary); }
.paper-research-item { padding: 12px 0; border-bottom: 1px solid var(--qd-border); overflow-wrap: anywhere; }
.paper-research-item p { margin-bottom: 8px; }
.paper-research-select { width: 100%; margin-bottom: 16px; }
.paper-research-detail { overflow-wrap: anywhere; }
.paper-research-news, .paper-research-results { min-width: 0; }
@media (max-width: 900px) { .paper-research-grid { grid-template-columns: 1fr; } }
.paper-steps { list-style: none; padding: 0; line-height: 2; }
small { font-size: 12px; color: var(--qd-text-secondary); }
@media (max-width: 900px) { .paper-metrics { grid-template-columns: repeat(2, 1fr); } .paper-workspace { grid-template-columns: 1fr; } .paper-heading { flex-wrap: wrap; } .paper-health { text-align: left; } .paper-health button { margin-left: 0; } }
</style>
