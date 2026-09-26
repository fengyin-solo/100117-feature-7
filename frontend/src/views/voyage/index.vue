<template>
  <section class="page" data-module="voyage">
    <header class="page-head">
      <div>
        <h2>航次管理</h2>
        <p class="page-desc">按预计到港与实际到港维护航次：确认开航进入航行中、到港后进入已到港、结航后归档；状态只进不退，船舶在港状态自动同步。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出航次管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>航次编号</span>
        <input v-model="keyword" placeholder="按航次编号检索" />
      </label>
      <label class="filter-item">
        <span>航次状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>航次编号</th>
          <th>关联船舶</th>
          <th>进口/出口航次号</th>
          <th>预计到港</th>
          <th>实际开航</th>
          <th>实际到港</th>
          <th>结航时间</th>
          <th>航次状态</th>
          <th>最近变更</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['航次编号'] ?? '—' }}</td>
          <td>{{ row['关联船舶'] ?? '—' }}</td>
          <td>{{ row['进口航次号'] ?? '—' }} / {{ row['出口航次号'] || '—' }}</td>
          <td>{{ row['预计到港'] || '—' }}</td>
          <td>{{ row['实际开航'] || '—' }}</td>
          <td>{{ row['实际到港'] || '—' }}</td>
          <td>{{ row['结航时间'] || '—' }}</td>
          <td><span class="status-tag" :data-status="row.status">{{ row.status }}</span></td>
          <td>
            <template v-for="record in [lastRecord(row)]" :key="record ? record['时间'] : 'empty'">
              <template v-if="record">
                {{ record['时间'] }}<br />
                <small>{{ record['操作人'] }} · {{ record['来源'] }}</small>
              </template>
              <span v-else>—</span>
            </template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in allowedActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openBackfill(row)">补录时间</button>
            <button class="link" type="button" @click="openBackfill(row, true)">流转记录</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="10" class="empty-state">暂无符合条件的航次数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条航次记录</span>
      <span v-if="notice" class="notice-text">{{ notice }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="backfillOpen" class="modal-mask" @click.self="closeBackfill">
      <div class="modal">
        <h3>时间补录与状态重排 · {{ backfillTitle }}</h3>
        <p class="modal-tip">
          补录实际开航、实际到港、结航时间后，系统按时间链路把状态重排到应有档位；
          实际到港早于开航、时间缺段或会导致状态退回时将拒绝并说明原因。
        </p>
        <div class="modal-grid">
          <label>
            <span>预计到港</span>
            <input v-model="form['预计到港']" type="datetime-local" />
          </label>
          <label>
            <span>实际开航</span>
            <input v-model="form['实际开航']" type="datetime-local" />
          </label>
          <label>
            <span>实际到港</span>
            <input v-model="form['实际到港']" type="datetime-local" />
          </label>
          <label>
            <span>结航时间</span>
            <input v-model="form['结航时间']" type="datetime-local" :disabled="readonlyBackfill" />
          </label>
          <label class="modal-operator">
            <span>操作人</span>
            <input v-model="form['操作人']" :disabled="readonlyBackfill" placeholder="当前值班人" />
          </label>
        </div>

        <div class="history-box">
          <h4>流转记录（操作人与时间留痕）</h4>
          <ul v-if="formHistory.length">
            <li v-for="(record, idx) in formHistory" :key="idx">
              <span class="status-tag" :data-status="record['新状态']">{{ record['原状态'] }} → {{ record['新状态'] }}</span>
              {{ record['动作'] }} · {{ record['时间'] }} · {{ record['操作人'] }}（{{ record['来源'] }}）
            </li>
          </ul>
          <p v-else class="empty-state">暂无流转记录</p>
        </div>

        <p v-if="backfillError" class="error-text">{{ backfillError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="closeBackfill">关闭</button>
          <button v-if="!readonlyBackfill" class="btn primary" type="button" :disabled="submitting" @click="submitBackfill">
            {{ submitting ? '提交中…' : '保存并重排状态' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null | Record<string, unknown>[]>
type HistoryRecord = Record<string, string>

const ENDPOINT = '/api/voyage'
const statuses = ['待开航', '航行中', '已到港', '已结航']
const NEXT_ACTIONS: Record<string, string[]> = {
  待开航: ['确认开航'],
  航行中: ['确认到港'],
  已到港: ['结航航次'],
  已结航: [],
}
const TIME_FIELDS = ['预计到港', '实际开航', '实际到港', '结航时间']

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const keyword = ref('')
const statusFilter = ref('')

const backfillOpen = ref(false)
const readonlyBackfill = ref(false)
const submitting = ref(false)
const backfillError = ref('')
const formRow = ref<Row | null>(null)
const form = reactive<Record<string, string>>({
  预计到港: '',
  实际开航: '',
  实际到港: '',
  结航时间: '',
  操作人: session.operator,
})

const stats = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  return [
    { label: '航行中航次', value: rows.value.filter((r) => r.status === '航行中').length },
    {
      label: '今日到港航次',
      value: rows.value.filter((r) => r.status === '已到港' || r.status === '已结航')
        .filter((r) => String(r['实际到港'] || '').startsWith(today)).length,
    },
    { label: '待结航航次', value: rows.value.filter((r) => r.status === '已到港').length },
  ]
})

function lastRecord(row: Row): HistoryRecord | null {
  const history = (row['流转记录'] ?? []) as unknown as HistoryRecord[]
  return history.length ? history[history.length - 1] : null
}

const formHistory = computed<HistoryRecord[]>(() =>
  ((formRow.value && formRow.value['流转记录']) ?? []) as unknown as HistoryRecord[],
)
const backfillTitle = computed(() => (formRow.value ? String(formRow.value['航次编号'] ?? '') : ''))

function allowedActions(row: Row): string[] {
  return NEXT_ACTIONS[String(row.status)] || []
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function toInputValue(value: unknown): string {
  // 后端存的是 "YYYY-MM-DD HH:MM"，datetime-local 需要 "YYYY-MM-DDTHH:MM"。
  return String(value || '').replace(' ', 'T').slice(0, 16)
}

function openBackfill(row: Row, readonly = false) {
  formRow.value = row
  readonlyBackfill.value = readonly || row.status === '已结航'
  backfillError.value = ''
  TIME_FIELDS.forEach((field) => {
    form[field] = toInputValue(row[field])
  })
  form['操作人'] = session.operator
  backfillOpen.value = true
}

function closeBackfill() {
  backfillOpen.value = false
  formRow.value = null
}

async function submitBackfill() {
  if (!formRow.value) return
  submitting.value = true
  backfillError.value = ''
  const payload: Record<string, string> = { 操作人: form['操作人'] || session.operator }
  TIME_FIELDS.forEach((field) => {
    if (form[field]) payload[field] = form[field]
  })
  try {
    const response = await request(`${ENDPOINT}/${formRow.value.id}/backfill`, {
      method: 'POST',
      body: JSON.stringify({ values: payload }),
    })
    const result = await response.json()
    if (!response.ok || result.ok === false) {
      backfillError.value = result.detail || result.message || '时间补录未生效'
      return
    }
    notice.value = result.message
    backfillOpen.value = false
    await reload()
  } catch (error) {
    backfillError.value = error instanceof Error ? error.message : '时间补录请求失败'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, 操作人: session.operator } }),
    })
    const result = await response.json().catch(() => null)
    if (!response.ok || !result) {
      errorMessage.value = '航次动作未生效，请稍后重试'
      return
    }
    if (result.ok === false) {
      errorMessage.value = result.message
      return
    }
    notice.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '航次操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  query.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('航次列表读取失败')
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
.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  background: #e9ecef;
  color: #495057;
  white-space: nowrap;
}
.status-tag[data-status='待开航'] { background: #fff3bf; color: #8a6100; }
.status-tag[data-status='航行中'] { background: #d0ebff; color: #1864ab; }
.status-tag[data-status='已到港'] { background: #d3f9d8; color: #2b8a3e; }
.status-tag[data-status='已结航'] { background: #e9ecef; color: #868e96; }

.filter-item select {
  height: 32px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  padding: 0 8px;
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
  width: 640px;
  max-width: calc(100vw - 32px);
  max-height: 86vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 20px 24px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
}
.modal h3 { margin: 0 0 8px; }
.modal-tip { color: #667085; font-size: 13px; line-height: 1.6; }
.modal-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 16px;
  margin: 12px 0;
}
.modal-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #344054; }
.modal-operator { grid-column: span 2; }
.modal-grid input {
  height: 34px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  padding: 0 10px;
}
.modal-grid input:disabled { background: #f5f6f8; color: #98a2b3; }
.history-box {
  border: 1px solid #eaecf0;
  border-radius: 8px;
  padding: 10px 14px;
  margin: 8px 0 12px;
  background: #fafbfc;
}
.history-box h4 { margin: 0 0 8px; font-size: 13px; }
.history-box ul { margin: 0; padding-left: 0; list-style: none; }
.history-box li { font-size: 13px; line-height: 2; color: #475467; }
.history-box .status-tag { margin-right: 8px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.notice-text { color: #1864ab; }
</style>
