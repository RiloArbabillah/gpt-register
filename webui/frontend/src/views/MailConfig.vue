<script setup>
import { onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getMailConfig, saveMailConfig, testMail } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'
import { useLocaleStore } from '@/stores/locale'

const source = ref('outlook')
const locale = useLocaleStore()
const cfApiUrl = ref('')
const cfDomain = ref('')
const cfAdminToken = ref('')
const tokenPlaceholder = ref('Worker 配置的 ADMIN_PASSWORDS')
const imapHost = ref('')
const imapPort = ref('993')
const imapUsername = ref('')
const imapPassword = ref('')
const imapDomain = ref('')
const imapPasswordPlaceholder = ref('IMAP password')
const saving = ref(false)
const testing = ref(false)

async function load() {
  try {
    const { config } = await getMailConfig()
    source.value = config.mail_source || 'outlook'
    cfApiUrl.value = config.cf_api_url || ''
    cfDomain.value = config.cf_domain || ''
    cfAdminToken.value = ''
    tokenPlaceholder.value = config.cf_admin_token === '***'
      ? '已设置（留空不修改）' : 'Worker 配置的 ADMIN_PASSWORDS'
    imapHost.value = config.imap_host || ''
    imapPort.value = config.imap_port || '993'
    imapUsername.value = config.imap_username || ''
    imapPassword.value = ''
    imapDomain.value = config.imap_domain || ''
    imapPasswordPlaceholder.value = config.imap_password === '***'
      ? '已设置（留空不修改）' : 'IMAP password'
  } catch (e) { ElMessage.error(e.message) }
}

async function save() {
  const isCf = source.value === 'cf_temp'
  const isImap = source.value === 'imap'
  saving.value = true
  try {
    await saveMailConfig({
      mail_source: source.value,
      cf_api_url: isCf ? cfApiUrl.value.trim() : '',
      cf_admin_token: isCf ? (cfAdminToken.value.trim() || '***') : '***',
      cf_domain: isCf ? cfDomain.value.trim() : '',
      imap_host: isImap ? imapHost.value.trim() : '',
      imap_port: isImap ? imapPort.value.trim() : '',
      imap_username: isImap ? imapUsername.value.trim() : '',
      imap_password: isImap ? (imapPassword.value || '***') : '***',
      imap_domain: isImap ? imapDomain.value.trim() : '',
    })
    ElMessage.success(locale.t('saved'))
    load()
  } catch (e) { ElMessage.error(e.message) }
  finally { saving.value = false }
}

async function test() {
  testing.value = true
  try { const r = await testMail(); ElMessage.success(r.message || locale.t('connectionOk')) }
  catch (e) { ElMessage.error(e.message) }
  finally { testing.value = false }
}

onActivated(() => load())
</script>

<template>
  <div class="page">
    <el-card shadow="never" style="max-width: 720px">
      <template #header><span class="section-title" style="margin: 0">{{ locale.t('mailConfig') }}</span></template>
      <p class="hint">
        OpenAI 注册需要邮箱收 OTP。可选 Outlook 接码池、自建 CF Worker，或 IMAP catch-all。
      </p>
      <el-form label-position="top">
        <el-form-item :label="locale.t('mailSource')">
          <el-radio-group v-model="source">
            <el-radio value="outlook">Outlook 接码池</el-radio>
            <el-radio value="cf_temp">CF Temp Email（自建 catch-all）</el-radio>
            <el-radio value="imap">{{ locale.t('imapCatchAll') }}</el-radio>
          </el-radio-group>
        </el-form-item>

        <template v-if="source === 'cf_temp'">
          <el-form-item label="API URL（Worker 部署地址）">
            <el-input v-model="cfApiUrl" placeholder="https://mail.example.com" />
          </el-form-item>
          <el-form-item label="Admin Token（Worker 环境变量 ADMIN_PASSWORDS）">
            <el-input v-model="cfAdminToken" type="password" show-password :placeholder="tokenPlaceholder" />
          </el-form-item>
          <el-form-item label="Catch-all 域名">
            <el-input v-model="cfDomain" placeholder="example.com" />
          </el-form-item>
          <el-alert
            type="warning" :closable="false" show-icon
            title="需在 Cloudflare Email Routing 配置 catch-all 转发到 Worker，否则收不到邮件。"
          />
        </template>

        <template v-if="source === 'imap'">
          <el-form-item label="IMAP Host">
            <el-input v-model="imapHost" placeholder="imap.example.com" />
          </el-form-item>
          <el-form-item label="IMAP Port">
            <el-input v-model="imapPort" inputmode="numeric" placeholder="993" />
          </el-form-item>
          <el-form-item label="IMAP Username">
            <el-input v-model="imapUsername" placeholder="inbox@example.com" />
          </el-form-item>
          <el-form-item label="IMAP Password">
            <el-input v-model="imapPassword" type="password" show-password :placeholder="imapPasswordPlaceholder" />
          </el-form-item>
          <el-form-item label="Catch-all domain">
            <el-input v-model="imapDomain" placeholder="example.com" />
          </el-form-item>
          <el-alert
            type="info" :closable="false" show-icon
            title="Setiap pendaftaran menggunakan alamat acak dengan akhiran -gpt@domain ini."
          />
        </template>

      </el-form>
    </el-card>

    <FooterToolbar>
      <template #left>{{ locale.t('mailSource') }}: {{ source === 'cf_temp' ? 'CF Temp Email' : source === 'imap' ? locale.t('imapCatchAll') : 'Outlook' }}</template>
      <el-button v-if="source === 'cf_temp' || source === 'imap'" :loading="testing" @click="test">{{ locale.t('testConnection') }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ locale.t('save') }}</el-button>
    </FooterToolbar>
  </div>
</template>
