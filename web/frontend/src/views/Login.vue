<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <h2 style="text-align:center">DevBoard Toolkit</h2>
      </template>
      <el-tabs v-model="activeTab">
        <el-tab-pane label="登录" name="login">
          <el-form @submit.prevent="doLogin">
            <el-form-item label="用户名">
              <el-input v-model="loginForm.username" placeholder="用户名" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="loginForm.password" type="password" show-password placeholder="密码" />
            </el-form-item>
            <el-button type="primary" native-type="submit" style="width:100%" :loading="loading">
              登录
            </el-button>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="注册" name="register">
          <el-form @submit.prevent="doRegister">
            <el-form-item label="用户名">
              <el-input v-model="regForm.username" placeholder="用户名" />
            </el-form-item>
            <el-form-item label="显示名">
              <el-input v-model="regForm.displayName" placeholder="显示名(可选)" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="regForm.password" type="password" show-password placeholder="密码" />
            </el-form-item>
            <el-button type="primary" native-type="submit" style="width:100%" :loading="loading">
              注册
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../utils/api'
import { useUserStore } from '../stores/user'

const router = useRouter()
const { setAuth } = useUserStore()
const activeTab = ref('login')
const loading = ref(false)

const loginForm = reactive({ username: '', password: '' })
const regForm = reactive({ username: '', displayName: '', password: '' })

async function doLogin() {
  loading.value = true
  try {
    const { data } = await api.post('/auth/login', loginForm)
    setAuth(data)
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  loading.value = true
  try {
    await api.post('/auth/register', {
      username: regForm.username,
      password: regForm.password,
      display_name: regForm.displayName,
    })
    ElMessage.success('注册成功,请登录')
    activeTab.value = 'login'
    loginForm.username = regForm.username
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea, #764ba2);
}
.login-card {
  width: 400px;
}
</style>
