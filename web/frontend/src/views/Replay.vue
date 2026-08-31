<template>
  <div style="display:flex;flex-direction:column;height:100%;gap:12px">
    <!-- 板池状态卡片 -->
    <el-card>
      <template #header>
        <div style="display:flex;align-items:center">
          <span>板池状态</span>
          <el-button size="small" style="margin-left:auto" :loading="checking" @click="checkBoards">
            检测空闲板
          </el-button>
        </div>
      </template>
      <div style="display:flex;flex-wrap:wrap;gap:12px">
        <el-tag v-for="b in boards" :key="b.name"
          :type="b.status === 'idle' ? 'success' : b.status === 'busy' ? 'warning' : 'danger'"
          size="large" effect="dark">
          {{ b.name }} ({{ b.status }})
          <span v-if="b.locked_by" style="font-size:11px;margin-left:4px">— {{ b.locked_by }}</span>
        </el-tag>
      </div>
    </el-card>

    <!-- 回灌控制 -->
    <el-card>
      <el-form label-width="100px">
        <el-form-item label="回灌文件夹">
          <el-input v-model="form.folder" placeholder="例: \\172.17.188.71\share\replay_data" />
        </el-form-item>
        <el-form-item label="使用板数">
          <el-input-number v-model="form.board_count" :min="0" :max="10" />
          <span style="margin-left:8px;color:#999;font-size:12px">0 = 自动选最大空闲数</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="starting" @click="startReplay">启动回灌</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 实时日志 -->
    <el-card style="flex:1;overflow:auto">
      <template #header>
        <div style="display:flex;align-items:center">
          <span>实时日志</span>
          <el-tag :type="connected ? 'success' : 'info'" size="small" style="margin-left:8px">
            {{ connected ? '已连接' : '断开' }}
          </el-tag>
          <el-button size="small" style="margin-left:auto" @click="clearLogs">清空</el-button>
        </div>
      </template>
      <div class="log-box">
        <div v-for="(log, i) in replayLogs" :key="i" class="log-line" :class="log.level">
          <span class="log-ts">{{ log.ts }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../utils/api'
import { useLogs, clearLogs } from '../utils/ws'

const { logs, connected } = useLogs()
const replayLogs = computed(() => logs.value.filter(l => l.channel === 'replay' || l.channel === 'boards'))

const boards = ref([])
const checking = ref(false)
const starting = ref(false)
const form = ref({ folder: '', board_count: 0 })

async function loadBoards() {
  try {
    const { data } = await api.get('/boards')
    boards.value = data.boards
  } catch (e) { /* ignore */ }
}

async function checkBoards() {
  checking.value = true
  try {
    await api.post('/boards/check')
    setTimeout(loadBoards, 3000)
  } catch (e) {
    ElMessage.error('检测失败')
  } finally { checking.value = false }
}

async function startReplay() {
  if (!form.value.folder) { ElMessage.warning('请填写回灌文件夹'); return }
  starting.value = true
  try {
    const { data } = await api.post('/replay/start', form.value)
    ElMessage.success(data.msg + (data.boards ? ': ' + data.boards.join(', ') : ''))
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  } finally { starting.value = false }
}

onMounted(loadBoards)
setInterval(loadBoards, 5000)
</script>

<style scoped>
.log-box { font-family: Consolas, monospace; font-size: 13px; max-height: 400px; overflow-y: auto; }
.log-line { padding: 2px 0; }
.log-ts { color: #888; margin-right: 8px; }
.log-msg { color: #333; }
.log-line.error .log-msg { color: #f56c6c; }
</style>
