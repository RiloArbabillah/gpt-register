<script setup>
import { onActivated, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { importImapAccounts, listImapAccounts, resetImapAccounts, deleteImapAccount, bulkDeleteImapAccounts } from '@/api/settings'
import { useRuntimeStore } from '@/stores/runtime'

const runtime = useRuntimeStore()
const text = ref('')
const rows = ref([])
const selected = ref([])
const status = ref('')
const loading = ref(false)
const importing = ref(false)
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const STATUS_TYPE = { available: 'success', in_use: 'warning', done: 'primary', failed: 'danger' }

async function load(reset = false) {
  if (reset) page.value = 1
  loading.value = true
  try {
    const result = await listImapAccounts({ status: status.value, limit: PAGE_SIZE, offset: (page.value - 1) * PAGE_SIZE })
    rows.value = result.items; total.value = result.total
  } catch (e) { ElMessage.error(e.message) } finally { loading.value = false }
}
async function confirm(message) {
  try { await ElMessageBox.confirm(message, 'Confirm', { type: 'warning' }); return true } catch (_) { return false }
}
async function doImport() {
  if (!text.value.trim()) return
  importing.value = true
  try { const r = await importImapAccounts(text.value.trim()); ElMessage.success(`Parsed ${r.parsed}, added ${r.inserted}, updated ${r.updated}`); text.value = ''; load(true); runtime.bumpData() }
  catch (e) { ElMessage.error(e.message) } finally { importing.value = false }
}
async function resetSelected() {
  const emails = selected.value.map((row) => row.email)
  if (!emails.length || !(await confirm(`Reset ${emails.length} selected mailbox(es) to available?`))) return
  try { const r = await resetImapAccounts(emails); ElMessage.success(`Reset ${r.reset}`); load() } catch (e) { ElMessage.error(e.message) }
}
async function resetFiltered() {
  if (!['done', 'failed'].includes(status.value) || !(await confirm(`Reset all ${status.value} mailbox(es) to available?`))) return
  try { const r = await resetImapAccounts(null, status.value); ElMessage.success(`Reset ${r.reset}`); load(true) } catch (e) { ElMessage.error(e.message) }
}
async function deleteSelected() {
  const emails = selected.value.map((row) => row.email)
  if (!emails.length || !(await confirm(`Delete ${emails.length} selected mailbox(es)?`))) return
  try { const r = await bulkDeleteImapAccounts({ emails }); ElMessage.success(`Deleted ${r.deleted}`); load() } catch (e) { ElMessage.error(e.message) }
}
async function deleteOne(email) {
  if (!(await confirm(`Delete ${email}?`))) return
  try { await deleteImapAccount(email); load() } catch (e) { ElMessage.error(e.message) }
}
watch(page, () => load())
watch(status, () => load(true))
onActivated(() => load())
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <el-col :md="9" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">Import IMAP Pool</span></template>
          <p class="hint">One mailbox per line: <code>email----password----host----port</code></p>
          <el-input v-model="text" type="textarea" :rows="10" class="mono" placeholder="user@example.com----password----imap.example.com----993" />
          <el-button type="primary" :loading="importing" style="margin-top: 12px" @click="doImport">Import</el-button>
        </el-card>
      </el-col>
      <el-col :md="15">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">IMAP Mailboxes</span></template>
          <el-space wrap style="margin-bottom: 12px">
            <el-select v-model="status" style="width: 130px"><el-option label="All" value="" /><el-option label="Available" value="available" /><el-option label="In use" value="in_use" /><el-option label="Done" value="done" /><el-option label="Failed" value="failed" /></el-select>
            <el-button @click="load(false)"><el-icon><Refresh /></el-icon>Refresh</el-button>
            <el-button :disabled="!selected.length" @click="resetSelected">Reset Selected</el-button>
            <el-button :disabled="!['done', 'failed'].includes(status)" @click="resetFiltered">Reset Filtered</el-button>
            <el-button type="danger" plain :disabled="!selected.length" @click="deleteSelected">Delete Selected</el-button>
          </el-space>
          <el-table v-loading="loading" :data="rows" size="small" stripe @selection-change="(value) => (selected = value)">
            <el-table-column type="selection" width="44" />
            <el-table-column prop="email" label="Email" min-width="190" show-overflow-tooltip />
            <el-table-column prop="host" label="Host" min-width="150" show-overflow-tooltip />
            <el-table-column prop="port" label="Port" width="70" />
            <el-table-column label="Status" width="100"><template #default="{ row }"><el-tag size="small" :type="STATUS_TYPE[row.status] || 'info'">{{ row.status }}</el-tag></template></el-table-column>
            <el-table-column label="Action" width="90"><template #default="{ row }"><el-button size="small" text type="danger" @click="deleteOne(row.email)">Delete</el-button></template></el-table-column>
          </el-table>
          <div style="display: flex; justify-content: center; margin-top: 14px"><el-pagination v-model:current-page="page" :page-size="PAGE_SIZE" :total="total" layout="prev, pager, next, total" background /></div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
