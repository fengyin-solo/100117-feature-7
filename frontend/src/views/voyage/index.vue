<template>
  <section class="page" data-module="voyage">
    <header class="page-head">
      <div>
        <h2>航次管理</h2>
        <p class="page-desc">围绕开航、到港、结航做状态流转与时间补录：状态只进不退，每次变更均记录操作人与时间，船舶在港状态同步更新。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记航次</button>
        <button class="btn" type="button" @click="reconcileAll">按实际时间批量重排</button>
        <button class="btn" type="button" @click="exportRows">导出航次清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayValue(row, column) }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(String(row.status))"
              :key="action.name"
              class="link"
              type="button"
              @click="openAction(action.name, row)"
            >
              {{ action.name }}
            </button>
            <button class="link" type="button" @click="openBackfill(row)">补录时间</button>
            <button class="link" type="button" @click="openHistory(row)">流转记录</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无航次数据，可先登记航次</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条航次记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <!-- 单步动作：确认开航 / 确认到港 / 结航航次 -->
    <div v-if="dialog.mode" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <h3>{{ dialog.mode === 'action' ? dialog.action : '补录开航/到港/结航时间' }}</h3>
        <p class="modal-tip" v-if="dialog.mode === 'action'">
          {{ dialog.row?.['航次编号'] }} · 当前状态「{{ dialog.row?.status }}」
        </p>

        <template v-if="dialog.mode === 'action'">
          <label class="form-item" v-if="dialog.action === '确认开航'">
            <span>实际开航时间</span>
            <input v-model="dialog.form.实际开航" type="datetime-local" />
            <em class="form-hint">留空则取当前时间</em>
          </label>
          <label class="form-item" v-if="dialog.action === '确认到港'">
            <span>实际到港时间 <i>*</i></span>
            <input v-model="dialog.form.实际到港" type="datetime-local" />
            <em class="form-hint">早于实际开航或缺失时将被拒绝</em>
          </label>
          <label class="form-item" v-if="dialog.action === '结航航次'">
            <span>结航时间</span>
            <input v-model="dialog.form.结航时间" type="datetime-local" />
            <em class="form-hint">留空则取当前时间；重复结航只生效一次</em>
          </label>
        </template>

        <template v-else>
          <p class="modal-tip">{{ dialog.row?.['航次编号'] }} · 当前状态「{{ dialog.row?.status }}」，状态只能向前重排</p>
          <label class="form-item">
            <span>预计到港</span>
            <input v-model="dialog.form.预计到港" type="datetime-local" />
          </label>
          <label class="form-item">
            <span>实际开航</span>
            <input v-model="dialog.form.实际开航" type="datetime-local" />
          </label>
          <label class="form-item">
            <span>实际到港</span>
            <input v-model="dialog.form.实际到港" type="datetime-local" />
          </label>
          <label class="form-item">
            <span>结航时间</span>
            <input v-model="dialog.form.结航时间" type="datetime-local" />
          </label>
        </template>

        <label class="form-item">
          <span>操作人 <i>*</i></span>
          <input v-model="dialog.form.operator" placeholder="请输入操作人" />
        </label>

        <p v-if="dialog.error" class="error-text modal-error">{{ dialog.error }}</p>

        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeDialog">取消</button>
          <button class="btn primary" type="button" :disabled="dialog.submitting" @click="submitDialog">
            {{ dialog.submitting ? '提交中…' : '确认提交' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 流转记录 -->
    <div v-if="history.open" class="modal-mask" @click.self="history.open = false">
      <div class="modal">
        <h3>流转记录 · {{ history.row?.['航次编号'] }}</h3>
        <table class="data-table history-table" v-if="historyRecords.length">
          <thead>
            <tr>
              <th>动作</th>
              <th>状态变化</th>
              <th>业务时间</th>
              <th>操作人</th>
              <th>操作时间</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(record, index) in historyRecords" :key="index">
              <td>{{ record.action }}</td>
              <td>{{ record.from }} → {{ record.to }}</td>
              <td>{{ record.business_time || '—' }}</td>
              <td>{{ record.operator }}</td>
              <td>{{ record.operated_at }}</td>
              <td>{{ record.source }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="modal-tip">该航次暂无状态变更记录。</p>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="history.open = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null | HistoryRecord[]>
type HistoryRecord = {
  action: string
  from: string
  to: string
  operator: string
  operated_at: string
  business_time?: string
  source?: string
}

const ENDPOINT = '/api/voyage'
const columns = ['航次编号', '关联船舶', '航次状态', '预计到港', '实际开航', '实际到港', '结航时间', '航线名称']
const filterFields = ['航次编号', '关联船舶', '进口航次号']

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = ref<Record<string, string>>({})

// 每档状态只露出“下一档”动作；已结航后没有任何流转动作，保证不能退回或重复结航
const NEXT_ACTION: Record<string, string> = {
  待开航: '确认开航',
  航行中: '确认到港',
  已到港: '结航航次',
}

function availableActions(status: string): { name: string }[] {
  return NEXT_ACTION[status] ? [{ name: NEXT_ACTION[status] }] : []
}

const stats = computed(() => {
  const sailing = rows.value.filter((row) => row.status === '航行中').length
  const arrived = rows.value.filter((row) => row.status === '已到港').length
  const waiting = rows.value.filter((row) => row.status === '待开航').length
  return [
    { label: '待开航航次', value: waiting },
    { label: '航行中航次', value: sailing },
    { label: '待结航航次（已到港）', value: arrived },
  ]
})

const dialog = reactive({
  mode: '' as '' | 'action' | 'backfill',
  action: '',
  row: null as Row | null,
  submitting: false,
  error: '',
  form: {
    operator: session.operator,
    预计到港: '',
    实际开航: '',
    实际到港: '',
    结航时间: '',
  },
})

const history = reactive({ open: false, row: null as Row | null })

const historyRecords = computed<HistoryRecord[]>(() => {
  const raw = history.row?.['流转记录']
  return Array.isArray(raw) ? (raw as HistoryRecord[]) : []
})

function toLocalInput(value: unknown): string {
  if (!value) return ''
  return String(value).replace(' ', 'T').slice(0, 16)
}

function displayValue(row: Row, column: string): string {
  if (column === '航次状态') return String(row.status ?? row[column] ?? '—')
  const value = row[column]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function resetDialogForm(row?: Row) {
  dialog.form = {
    operator: session.operator,
    预计到港: row ? toLocalInput(row['预计到港']) : '',
    实际开航: row ? toLocalInput(row['实际开航']) : '',
    实际到港: row ? toLocalInput(row['实际到港']) : '',
    结航时间: row ? toLocalInput(row['结航时间']) : '',
  }
  dialog.error = ''
}

function openAction(action: string, row: Row) {
  dialog.mode = 'action'
  dialog.action = action
  dialog.row = row
  dialog.submitting = false
  resetDialogForm(row)
}

function openBackfill(row: Row) {
  dialog.mode = 'backfill'
  dialog.action = ''
  dialog.row = row
  dialog.submitting = false
  resetDialogForm(row)
}

function openHistory(row: Row) {
  history.open = true
  history.row = row
}

function closeDialog() {
  dialog.mode = ''
  dialog.row = null
  dialog.error = ''
  dialog.submitting = false
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '航次登记入口尚未接入审批流'
}

async function readMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    return payload.message || payload.detail || fallback
  } catch {
    return fallback
  }
}

async function submitDialog() {
  if (!dialog.row) return
  dialog.error = ''
  if (!dialog.form.operator.trim()) {
    dialog.error = '请填写操作人，状态变更必须留痕'
    return
  }
  const id = Number(dialog.row.id)
  dialog.submitting = true
  try {
    let url = ''
    const body: Record<string, unknown> = { operator: dialog.form.operator.trim() }
    if (dialog.mode === 'action') {
      url = `${ENDPOINT}/${id}/actions`
      body.action = dialog.action
      if (dialog.action === '确认开航' && dialog.form.实际开航) body.实际开航 = dialog.form.实际开航
      if (dialog.action === '确认到港') body.实际到港 = dialog.form.实际到港
      if (dialog.action === '结航航次' && dialog.form.结航时间) body.结航时间 = dialog.form.结航时间
    } else {
      url = `${ENDPOINT}/${id}/backfill`
      for (const field of ['预计到港', '实际开航', '实际到港', '结航时间'] as const) {
        if (dialog.form[field]) body[field] = dialog.form[field]
      }
    }
    const response = await request(url, { method: 'POST', body: JSON.stringify({ values: body }) })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      dialog.error = payload?.message || payload?.detail || '操作未生效，请稍后重试'
      return
    }
    successMessage.value = payload.message
    closeDialog()
    await reload()
  } catch (error) {
    dialog.error = error instanceof Error ? error.message : '操作失败'
  } finally {
    dialog.submitting = false
  }
}

async function reconcileAll() {
  errorMessage.value = ''
  successMessage.value = ''
  if (!session.operator.trim()) {
    errorMessage.value = '缺少操作人，无法执行批量重排'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/reconcile`, {
      method: 'POST',
      body: JSON.stringify({ operator: session.operator }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      errorMessage.value = payload?.message || '批量重排未生效'
      return
    }
    successMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量重排失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error(await readMessage(response, '航次列表读取失败'))
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航次列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.success-text {
  color: #1a7f37;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal {
  width: 480px;
  max-width: calc(100vw - 32px);
  max-height: 85vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 20px 24px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.25);
}

.modal h3 {
  margin: 0 0 8px;
}

.modal-tip {
  color: #57606a;
  font-size: 13px;
  margin: 4px 0 12px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
  font-size: 13px;
}

.form-item i {
  color: #cf222e;
  font-style: normal;
}

.form-hint {
  color: #8c959f;
  font-size: 12px;
  font-style: normal;
}

.modal-error {
  margin: 4px 0 8px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.history-table {
  font-size: 12px;
}
</style>
