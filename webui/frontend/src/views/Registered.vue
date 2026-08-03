<script setup>
import { computed, onActivated, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listRegistered, getRegistered, getRegisteredEmails, deleteRegistered,
  bulkDeleteRegistered, checkPlus, recoverRefreshToken, recoverRefreshTokenBatch,
} from '@/api/register'
import { copyText, fmtTime } from '@/api/request'
import { useFormStore } from '@/stores/form'
import { useRuntimeStore } from '@/stores/runtime'
import StatusDot from '@/components/StatusDot.vue'

const { form } = storeToRefs(useFormStore())
const { dataVersion } = storeToRefs(useRuntimeStore())

const PAGE_SIZE = 20
const rows = ref([])
const total = ref(0)
const page = ref(1)
const filter = ref('all')
const selected = ref([])
const loading = ref(false)
const checking = ref(false)
const recovering = ref(false)
const checkResult = ref('')
const emailLoading = ref({})
const emailVisible = ref(false)
const emailAccount = ref('')
const emailSource = ref('')
const emailMessages = ref([])
const emailRefreshing = ref(false)

const PLUS_TYPE = {
  plus_eligible: 'success', plus_active: 'primary', free: 'warning',
  banned: 'danger', error: 'danger',
}
const SOURCE_LABEL = { imap: 'IMAP', imap_pool: 'IMAP Pool', cf_temp: 'Cloudflare Mail', outlook: 'Outlook' }
function plusOf(row) { return row.plus_check || null }
function formatEmailTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function load(resetPage) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    const { items, total: t } = await listRegistered({
      limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE, filter: filter.value,
    })
    rows.value = items
    total.value = t
  } catch (e) { ElMessage.error(e.message) }
  finally { loading.value = false }
}

function collectEmails(mode) {
  if (mode === 'selected') return selected.value.map((r) => r.email)
  if (mode === 'unchecked') return rows.value.filter((r) => !plusOf(r)).map((r) => r.email)
  return rows.value.map((r) => r.email) // all（当前页）
}

async function doCheck(mode) {
  const emails = collectEmails(mode)
  if (!emails.length) { ElMessage.info('There are no accounts to check on this page'); return }
  checking.value = true
  checkResult.value = `Checking... (${emails.length} accounts)`
  try {
    const { results } = await checkPlus(emails, form.value.proxy.trim())
    let plus = 0, free = 0, banned = 0
    for (const [email, info] of Object.entries(results)) {
      const row = rows.value.find((r) => r.email === email)
      if (row) row.plus_check = info
      if (info.status === 'plus_eligible' || info.status === 'plus_active') plus++
      else if (info.status === 'banned') banned++
      else if (info.status === 'free') free++
    }
    checkResult.value = `Completed: ${plus} Plus eligible, ${free} Free, ${banned} banned`
  } catch (e) {
    checkResult.value = ''
    ElMessage.error('Check failed: ' + e.message)
  } finally { checking.value = false }
}

async function confirm(msg) {
  try { await ElMessageBox.confirm(msg, 'Confirm', { type: 'warning', confirmButtonText: 'Confirm', cancelButtonText: 'Cancel' }); return true }
  catch (_) { return false }
}
async function deleteOne(email) {
  if (!(await confirm(`Delete credentials for ${email}?`))) return
  try { await deleteRegistered(email); ElMessage.success('Deleted'); load() }
  catch (e) { ElMessage.error(e.message) }
}
async function deleteSelected() {
  const emails = selected.value.map((r) => r.email)
  if (!emails.length) return
  if (!(await confirm(`Delete credentials for the selected ${emails.length} accounts? This cannot be undone.`))) return
  try { const r = await bulkDeleteRegistered({ emails }); ElMessage.success(`Deleted ${r.deleted} credentials`); load() }
  catch (e) { ElMessage.error(e.message) }
}
async function deleteAll() {
  if (!(await confirm('This clears all registered credentials; the mailbox pool is not affected. Continue?'))) return
  if (!(await confirm('Confirm again: delete all credentials? This cannot be undone.'))) return
  try { const r = await bulkDeleteRegistered({ all: true }); ElMessage.success(`Cleared ${r.deleted} credentials`); load() }
  catch (e) { ElMessage.error(e.message) }
}

async function recoverRt(row) {
  if (row.rt_len > 0) return
  if (!(await confirm(`Log in again as ${row.email} to retrieve its refresh token?`))) return
  recovering.value = true
  try {
    const r = await recoverRefreshToken({
      email: row.email,
      proxy: form.value.proxy.trim(),
      use_direct_connection: form.value.useDirectConnection,
      otp_timeout: parseInt(form.value.otpTimeout, 10) || 10,
    })
    ElMessage.info(`Refresh token retrieval started for ${r.email}`)
    useRuntimeStore().streamRun(r.run_id)
  } catch (e) { ElMessage.error(e.message) }
  finally { recovering.value = false }
}
async function recoverSelectedRt() {
  const emails = selected.value.filter((row) => !row.rt_len).map((row) => row.email)
  if (!emails.length) return
  if (!(await confirm(`Retrieve refresh tokens for ${emails.length} selected account(s)?`))) return
  recovering.value = true
  try {
    const r = await recoverRefreshTokenBatch({
      emails, proxy: form.value.proxy.trim(), use_direct_connection: form.value.useDirectConnection,
      otp_timeout: parseInt(form.value.otpTimeout, 10) || 10,
    })
    ElMessage.info(`RT recovery started for ${r.count} account(s)`)
    useRuntimeStore().streamRun(r.run_id)
  } catch (e) { ElMessage.error(e.message) }
  finally { recovering.value = false }
}

async function refreshEmailMessages(email, closeOnError = true) {
  emailRefreshing.value = true
  try {
    const result = await getRegisteredEmails(email, 10)
    emailSource.value = SOURCE_LABEL[result.source] || result.source || 'Mailbox'
    emailMessages.value = result.messages || []
  } catch (e) {
    if (closeOnError) emailVisible.value = false
    ElMessage.error('Failed to fetch emails: ' + e.message)
  } finally {
    emailRefreshing.value = false
  }
}

async function checkEmail(email) {
  emailLoading.value = { ...emailLoading.value, [email]: true }
  emailAccount.value = email
  emailVisible.value = true
  emailMessages.value = []
  await refreshEmailMessages(email)
  const next = { ...emailLoading.value }
  delete next[email]
  emailLoading.value = next
}

// 凭证弹窗
const credVisible = ref(false)
const credEmail = ref('')
const credData = ref(null)
const CRED_KEYS = ['access_token', 'session_token', 'refresh_token', 'id_token', 'device_id', 'csrf_token', 'cookie_header', 'password']
const credRows = computed(() => {
  if (!credData.value) return []
  return CRED_KEYS.filter((k) => credData.value[k]).map((k) => ({ key: k, val: credData.value[k] }))
})
async function viewCred(email) {
  try {
    const { data } = await getRegistered(email)
    credData.value = data
    credEmail.value = email
    credVisible.value = true
  } catch (e) { ElMessage.error('Failed to load credentials: ' + e.message) }
}
async function copyCell(email, field) {
  try {
    const { data } = await getRegistered(email)
    const val = data[field] || ''
    if (!val) { ElMessage.warning(`${field} is empty`); return }
    await copyText(val)
  } catch (e) { ElMessage.error('Failed to load credentials: ' + e.message) }
}
function copyAllJson() {
  if (credData.value) copyText(JSON.stringify(credData.value, null, 2))
}

watch(page, () => load())
watch(dataVersion, () => load())
onActivated(() => load())
</script>
<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span class="section-title" style="margin: 0">注册结果</span></template>

      <el-space wrap style="margin-bottom: 12px">
        <el-button @click="load(false)"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-select v-model="filter" style="width: 130px" @change="load(true)">
          <el-option label="全部" value="all" />
          <el-option label="有 RT" value="has_rt" />
          <el-option label="无 RT" value="no_rt" />
          <el-option label="未检测" value="unchecked" />
          <el-option label="Free" value="free" />
          <el-option label="可领Plus" value="plus" />
          <el-option label="已封号" value="banned" />
        </el-select>
        <el-button :loading="checking" @click="doCheck('unchecked')">Check Unchecked</el-button>
        <el-button :loading="checking" @click="doCheck('all')">Check Again</el-button>
        <el-button :loading="checking" :disabled="!selected.length" @click="doCheck('selected')">
          Check Selected ({{ selected.length }})
        </el-button>
        <el-button :loading="recovering" :disabled="recovering || !selected.some((r) => !r.rt_len)" @click="recoverSelectedRt">
          Get RT Selected
        </el-button>
        <el-button type="danger" plain :disabled="!selected.length" @click="deleteSelected">
          Delete Selected ({{ selected.length }})
        </el-button>
        <el-button type="danger" plain @click="deleteAll">Clear All</el-button>
        <span class="hint">{{ checkResult }}</span>
      </el-space>

      <el-skeleton v-if="loading && !rows.length" :rows="6" animated style="padding: 8px 0" />
      <el-table
        v-else
        v-loading="loading" :data="rows" size="small" stripe
        @selection-change="(v) => (selected = v)"
      >
        <el-table-column type="selection" width="44" />
        <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
        <el-table-column label="Source" width="130">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ SOURCE_LABEL[row.source] || row.source || 'IMAP' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Plus状态" width="120">
          <template #default="{ row }">
            <StatusDot v-if="plusOf(row)" :type="PLUS_TYPE[plusOf(row).status] || 'info'" :text="plusOf(row).label" />
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="access" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.at_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'access_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.at_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="session" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.st_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'session_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.st_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="refresh" width="100" align="center">
          <template #default="{ row }">
            <el-button v-if="row.rt_len > 0" size="small" text type="primary" @click="copyCell(row.email, 'refresh_token')">
              <el-icon><CopyDocument /></el-icon>{{ row.rt_len }}
            </el-button>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text @click="viewCred(row.email)">查看凭证</el-button>
            <el-button size="small" text @click="checkEmail(row.email)" :loading="!!emailLoading[row.email]">Check Email</el-button>
            <el-button v-if="!row.rt_len" size="small" text type="primary" :loading="recovering" :disabled="recovering" @click="recoverRt(row)">Get RT</el-button>
            <el-button size="small" text type="danger" @click="deleteOne(row.email)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无注册结果，去「单次注册」或「全自动批量」跑号" :image-size="70" />
        </template>
      </el-table>
      <div style="display: flex; justify-content: center; margin-top: 14px">
        <el-pagination
          v-model:current-page="page" :page-size="PAGE_SIZE" :total="total"
          layout="prev, pager, next, total" background
        />
      </div>

      <el-dialog v-model="credVisible" :title="credEmail" width="760px" top="6vh">
        <template #header>
          <div style="display: flex; align-items: center; gap: 12px">
            <span class="mono" style="font-weight: 600">{{ credEmail }}</span>
            <el-button size="small" @click="copyAllJson">复制全部 JSON</el-button>
          </div>
        </template>
        <div v-for="r in credRows" :key="r.key" style="margin-bottom: 12px">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 4px">
            <span class="mono" style="font-weight: 600; color: var(--dango-pink-dark)">{{ r.key }}</span>
            <el-tag size="small" type="info">len={{ r.val.length }}</el-tag>
            <el-button size="small" @click="copyText(r.val)">复制</el-button>
          </div>
          <el-input :model-value="r.val" type="textarea" :rows="2" readonly class="mono" />
        </div>
        <el-empty v-if="!credRows.length" description="无凭证字段" />
      </el-dialog>

      <el-dialog v-model="emailVisible" width="820px" top="6vh">
        <template #header>
          <div style="display: flex; align-items: center; gap: 12px; width: 100%">
            <span style="font-weight: 600">Recent Emails: {{ emailAccount }}</span>
            <el-button size="small" :loading="emailRefreshing" @click="refreshEmailMessages(emailAccount, false)">
              <el-icon><Refresh /></el-icon>Refresh
            </el-button>
          </div>
        </template>
        <div class="hint" style="margin-bottom: 12px">{{ emailSource }} · {{ emailMessages.length }} message(s)</div>
        <el-empty v-if="!emailMessages.length" description="No recent emails found" />
        <el-collapse v-else>
          <el-collapse-item v-for="message in emailMessages" :key="message.id" :name="message.id">
            <template #title>
              <div style="display: flex; gap: 12px; align-items: center; width: 100%; min-width: 0">
                <strong style="min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ message.subject }}</strong>
                <span class="hint" style="margin-left: auto; flex-shrink: 0">{{ formatEmailTime(message.received_at) }}</span>
              </div>
            </template>
            <div class="hint" style="margin-bottom: 8px">From: {{ message.from || 'Unknown sender' }}</div>
            <div class="hint" style="margin-bottom: 8px">To: {{ (message.to || []).join(', ') || emailAccount }}</div>
            <p style="white-space: pre-wrap; word-break: break-word; margin: 0">{{ message.body || message.preview || '(empty message)' }}</p>
          </el-collapse-item>
        </el-collapse>
      </el-dialog>
    </el-card>
  </div>
</template>
