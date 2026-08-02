<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRuntimeStore } from '@/stores/runtime'
import { translateEnglish, useLocaleStore } from '@/stores/locale'

const runtime = useRuntimeStore()
const locale = useLocaleStore()
const { logs } = storeToRefs(runtime)
const boxRef = ref(null)
const displayedLogs = computed(() =>
  locale.locale === 'en'
    ? logs.value.map((log) => ({ ...log, text: translateEnglish(log.text) }))
    : logs.value,
)

// 新日志自动滚到底
watch(
  () => logs.value.length,
  async () => {
    await nextTick()
    const el = boxRef.value
    if (el) el.scrollTop = el.scrollHeight
  },
)
</script>

<template>
  <div class="log-wrap">
    <div class="log-head">
      <span class="section-title" style="margin: 0">{{ locale.t('logs') }}</span>
      <el-button size="small" text @click="runtime.clearLogs">{{ locale.t('clear') }}</el-button>
    </div>
    <div ref="boxRef" class="log-box">
      <div v-for="l in displayedLogs" :key="l.id" class="line" :class="l.kind">{{ l.text }}</div>
      <div v-if="!logs.length" class="line" style="color: #8a7">{{ locale.t('waitingLogs') }}</div>
    </div>
  </div>
</template>

<style scoped>
.log-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
</style>
