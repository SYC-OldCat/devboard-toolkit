<template>
  <el-container style="height:100vh">
    <el-header style="background:#409eff;color:#fff;display:flex;align-items:center">
      <h3 style="margin:0 20px 0 0">🛠 DevBoard Toolkit</h3>
      <span style="opacity:.8">{{ displayName }}</span>
      <el-button text style="color:#fff;margin-left:auto" @click="onLogout">退出</el-button>
    </el-header>
    <el-container>
      <el-aside width="180px" style="background:#304156">
        <el-menu :default-active="$route.path" router background-color="#304156" text-color="#bfcbd9" active-text-color="#409eff">
          <el-menu-item index="/replay"><el-icon><VideoPlay /></el-icon><span>自动回灌</span></el-menu-item>
          <el-menu-item index="/dataproc"><el-icon><Document /></el-icon><span>数据处理</span></el-menu-item>
          <el-menu-item index="/jenkins"><el-icon><Tools /></el-icon><span>Jenkins编译</span></el-menu-item>
          <el-menu-item index="/pipeline"><el-icon><Connection /></el-icon><span>组合流水线</span></el-menu-item>
          <el-menu-item index="/settings"><el-icon><Setting /></el-icon><span>设置</span></el-menu-item>
        </el-menu>
      </el-aside>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '../stores/user'
import { connectLog } from '../utils/ws'

const router = useRouter()
const { state, logout } = useUserStore()
const displayName = computed(() => state.displayName)

// 连接 WebSocket 日志
const ws = ref(null)
connectLog(ws)

function onLogout() {
  logout()
  router.push('/login')
}
</script>
