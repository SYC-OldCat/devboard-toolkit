<template>
  <div style="display:flex;flex-direction:column;height:100%;gap:12px">
    <el-card>
      <el-form label-width="100px">
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="jira">Jira 链接</el-radio>
            <el-radio value="video">视频路径</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="form.mode === 'jira' ? 'Jira链接' : '视频路径'">
          <el-input v-model="form.input_text" type="textarea" :rows="8"
            :placeholder="form.mode === 'jira' ? '每行一个 Jira 链接' : '每行一个视频路径'" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="starting" @click="start">开始处理</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card style="flex:1;overflow:auto">
      <template #header><span>处理日志</span></template>
      <div class="log-box">
        <div v-for="(log, i) in logs" :key="i" class="log-line" :class="log.level">
          <span class="log-ts">{{ log.ts }}</span>
          <span>{{ log.msg }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../utils/api'
import { useLogs } from '../utils/ws'

const { logs } = useLogs()
const dpLogs = computed(() => logs.value.filter(l => l.channel === 'dataproc'))

const starting = ref(false)
const form = ref({ mode: 'jira', input_text: '' })

async function start() {
  if (!form.value.input_text.trim()) { ElMessage.warning('请输入内容'); return }
  starting.value = true
  try {
    const { data } = await api.post('/dataproc/start', form.value)
    ElMessage.success(data.msg)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  } finally { starting.value = false }
}
</script>

<style scoped>
.log-box { font-family: Consolas, monospace; font-size: 13px; max-height: 400px; overflow-y: auto; }
.log-line { padding: 2px 0; }
.log-ts { color: #888; margin-right: 8px; }
</style>
