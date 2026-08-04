<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const username = ref('')
const password = ref('')

async function submit() {
  if (!username.value.trim() || !password.value) return
  try {
    await auth.login(username.value.trim(), password.value)
    await router.replace(String(route.query.redirect || '/'))
  } catch (error) {
    ElMessage.error(error.message || 'Login failed')
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="never">
      <template #header><span class="section-title">Sign In</span></template>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="Username">
          <el-input v-model="username" autocomplete="username" autofocus />
        </el-form-item>
        <el-form-item label="Password">
          <el-input v-model="password" type="password" show-password autocomplete="current-password" @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="auth.loading" style="width: 100%">Sign In</el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; background: var(--app-content-bg); padding: 24px; }
.login-card { width: min(100%, 380px); }
</style>
