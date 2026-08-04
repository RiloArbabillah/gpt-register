<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { getSmsBalance } from '@/api/settings'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const { stats } = storeToRefs(useStatsStore())
const { autoStatus } = storeToRefs(useRuntimeStore())
const smsBalance = ref(null)
const smsBalanceLoading = ref(false)
let smsBalanceTimer = null

const cards = computed(() => [
  { label: '总计', value: stats.value.total, color: 'var(--brand)', icon: 'Files' },
  { label: '可用 available', value: stats.value.available, color: '#4caf50', icon: 'CircleCheck' },
  { label: '进行中 in_use', value: stats.value.in_use, color: '#ff9800', icon: 'Loading' },
  { label: '已完成 done', value: stats.value.done, color: '#2196f3', icon: 'Select' },
  { label: '失败 failed', value: stats.value.failed, color: '#e53935', icon: 'CircleClose' },
])

const autoStateLabel = computed(() => ({
  stopped: '未运行', running: '运行中', paused: '已暂停',
}[autoStatus.value.state] || autoStatus.value.state))
const autoStateType = computed(() => ({
  stopped: 'info', running: 'success', paused: 'warning',
}[autoStatus.value.state] || 'info'))

const smsBalanceDisplay = computed(() => {
  const value = smsBalance.value?.balance
  if (value === null || value === undefined) return smsBalance.value?.configured === false ? 'Not configured' : 'Unavailable'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 8 })
})

async function refreshSmsBalance() {
  smsBalanceLoading.value = true
  try {
    smsBalance.value = await getSmsBalance()
  } catch (error) {
    smsBalance.value = { ok: false, configured: true, balance: null, error: error?.message || 'Request failed' }
  } finally {
    smsBalanceLoading.value = false
  }
}

onMounted(() => {
  refreshSmsBalance()
  smsBalanceTimer = setInterval(refreshSmsBalance, 60_000)
})

onBeforeUnmount(() => {
  if (smsBalanceTimer) clearInterval(smsBalanceTimer)
  smsBalanceTimer = null
})
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.label" :xs="12" :sm="8" :md="4" style="margin-bottom: 16px">
        <el-card class="stat-card" shadow="hover">
          <div style="display: flex; align-items: center; justify-content: space-between">
            <div>
              <div class="stat-value" :style="{ color: c.color }">{{ c.value }}</div>
              <div class="stat-label">{{ c.label }}</div>
            </div>
            <el-icon :size="30" :style="{ color: c.color, opacity: 0.5 }"><component :is="c.icon" /></el-icon>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :xs="24" :sm="12" :md="8" style="margin-bottom: 16px">
        <el-card class="stat-card" shadow="hover">
          <div style="display: flex; align-items: center; justify-content: space-between">
            <div>
              <div class="stat-value" style="color: #7b1fa2">{{ smsBalanceDisplay }}</div>
              <div class="stat-label">SMS Balance{{ smsBalance?.provider ? ` · ${smsBalance.provider}` : '' }}</div>
              <div v-if="smsBalance?.checked_at" class="hint">{{ new Date(smsBalance.checked_at).toLocaleTimeString() }}</div>
            </div>
            <el-button text circle :loading="smsBalanceLoading" title="Refresh SMS balance" @click="refreshSmsBalance">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">自动跑号状态</span></template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="状态"><StatusDot :type="autoStateType" :text="autoStateLabel" /></el-descriptions-item>
            <el-descriptions-item label="并发">{{ autoStatus.concurrency || 1 }}</el-descriptions-item>
            <el-descriptions-item label="成功">{{ autoStatus.registered_ok || 0 }}</el-descriptions-item>
            <el-descriptions-item label="失败">{{ autoStatus.registered_fail || 0 }}</el-descriptions-item>
          </el-descriptions>
          <div style="margin-top: 12px">
            <el-button type="primary" @click="router.push('/auto')">前往自动批量</el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">快捷操作</span></template>
          <el-space wrap>
            <el-button @click="router.push('/import')"><el-icon><Upload /></el-icon>导入邮箱</el-button>
            <el-button @click="router.push('/register')"><el-icon><VideoPlay /></el-icon>单次注册</el-button>
            <el-button @click="router.push('/pool')"><el-icon><Files /></el-icon>邮箱列表</el-button>
            <el-button @click="router.push('/registered')"><el-icon><CircleCheck /></el-icon>注册结果</el-button>
          </el-space>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
