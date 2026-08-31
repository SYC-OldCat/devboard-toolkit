<template>
  <div style="display:flex;flex-direction:column;gap:12px">
    <el-card>
      <template #header>
        <div style="display:flex;align-items:center">
          <span>用户配置 (config_user.yaml)</span>
          <el-button type="primary" size="small" style="margin-left:auto" @click="saveUser">保存</el-button>
        </div>
      </template>
      <el-input v-model="userYaml" type="textarea" :rows="15" style="font-family:Consolas,monospace" />
    </el-card>

    <el-card>
      <template #header>
        <div style="display:flex;align-items:center">
          <span>系统配置 (config_system.yaml)</span>
          <el-button type="primary" size="small" style="margin-left:auto" @click="saveSystem">保存</el-button>
        </div>
      </template>
      <el-input v-model="systemYaml" type="textarea" :rows="15" style="font-family:Consolas,monospace" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../utils/api'

const userYaml = ref('')
const systemYaml = ref('')

async function load() {
  try {
    const [u, s] = await Promise.all([
      api.get('/config/user'),
      api.get('/config/system'),
    ])
    // 后端返回 dict, 前端转成 YAML 文本展示
    userYaml.value = JSON.stringify(u.data, null, 2)
    systemYaml.value = JSON.stringify(s.data, null, 2)
  } catch (e) {
    ElMessage.error('加载配置失败')
  }
}

async function saveUser() {
  try {
    const data = JSON.parse(userYaml.value)
    await api.put('/config/user', data)
    ElMessage.success('用户配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.message || ''))
  }
}

async function saveSystem() {
  try {
    const data = JSON.parse(systemYaml.value)
    await api.put('/config/system', data)
    ElMessage.success('系统配置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.message || ''))
  }
}

onMounted(load)
</script>
