<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import en from 'element-plus/es/locale/lang/en'
import AdminLayout from '@/layouts/AdminLayout.vue'
import { translateEnglish, useLocaleStore } from '@/stores/locale'

const locale = useLocaleStore()
const route = useRoute()
const elementLocale = computed(() => locale.locale === 'en' ? en : zhCn)
let observer

function translateNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const translated = translateEnglish(node.nodeValue)
    if (translated !== node.nodeValue) node.nodeValue = translated
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return
  for (const attr of ['title', 'placeholder', 'aria-label']) {
    if (!node.hasAttribute(attr)) continue
    const translated = translateEnglish(node.getAttribute(attr))
    if (translated !== node.getAttribute(attr)) node.setAttribute(attr, translated)
  }
  for (const child of node.childNodes) translateNode(child)
}

onMounted(() => {
  if (locale.locale !== 'en') return
  translateNode(document.body)
  observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) translateNode(node)
      if (record.type === 'characterData') translateNode(record.target)
    }
  })
  observer.observe(document.body, { childList: true, subtree: true, characterData: true })
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view v-if="route.meta?.public" />
    <AdminLayout v-else />
  </el-config-provider>
</template>
