<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createUser, deleteUser, listUsers, updateUser } from '@/api/auth'

const users = ref([])
const loading = ref(false)
const form = ref({ username: '', password: '', role: 'user' })

async function load() {
  loading.value = true
  try { users.value = (await listUsers()).users || [] } catch (error) { ElMessage.error(error.message) }
  finally { loading.value = false }
}

async function add() {
  try {
    await createUser(form.value)
    form.value = { username: '', password: '', role: 'user' }
    ElMessage.success('User created')
    await load()
  } catch (error) { ElMessage.error(error.message) }
}

async function toggle(user) {
  try { await updateUser(user.id, { is_active: !user.is_active }); await load() }
  catch (error) { ElMessage.error(error.message) }
}

async function changeRole(user, role) {
  try { await updateUser(user.id, { role }); await load() }
  catch (error) { ElMessage.error(error.message); await load() }
}

async function resetPassword(user) {
  try {
    const { value } = await ElMessageBox.prompt(`New password for ${user.username}`, 'Reset Password', {
      inputType: 'password', inputPattern: /.{8,}/, inputErrorMessage: 'Password must be at least 8 characters',
    })
    await updateUser(user.id, { password: value })
    ElMessage.success('Password reset')
  } catch (error) { if (error !== 'cancel') ElMessage.error(error.message) }
}

async function remove(user) {
  try {
    await ElMessageBox.confirm(`Delete ${user.username}?`, 'Confirm', { type: 'warning' })
    await deleteUser(user.id)
    await load()
  } catch (error) { if (error !== 'cancel') ElMessage.error(error.message) }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <el-card shadow="never" style="max-width: 900px; margin-bottom: 16px">
      <template #header><span class="section-title">Create User</span></template>
      <el-form inline @submit.prevent="add">
        <el-form-item label="Username"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="Password"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="Role"><el-select v-model="form.role" style="width: 120px"><el-option label="User" value="user" /><el-option label="Admin" value="admin" /></el-select></el-form-item>
        <el-button type="primary" :disabled="!form.username || !form.password" @click="add">Create</el-button>
      </el-form>
    </el-card>
    <el-card shadow="never">
      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="username" label="Username" />
        <el-table-column label="Role"><template #default="{ row }"><el-select :model-value="row.role" size="small" style="width: 100px" @change="changeRole(row, $event)"><el-option label="User" value="user" /><el-option label="Admin" value="admin" /></el-select></template></el-table-column>
        <el-table-column label="Status"><template #default="{ row }">{{ row.is_active ? 'Active' : 'Disabled' }}</template></el-table-column>
        <el-table-column label="Actions" width="300"><template #default="{ row }">
          <el-button size="small" @click="toggle(row)">{{ row.is_active ? 'Disable' : 'Enable' }}</el-button>
          <el-button size="small" @click="resetPassword(row)">Reset Password</el-button>
          <el-button size="small" type="danger" @click="remove(row)">Delete</el-button>
        </template></el-table-column>
      </el-table>
    </el-card>
  </div>
</template>
