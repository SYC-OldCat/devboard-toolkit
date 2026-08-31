/**
 * WebSocket 日志连接 — 接收后端推送的实时日志
 */
import { ref } from 'vue'

const logs = ref([])
const connected = ref(false)

export function connectLog(wsRef) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${window.location.host}/ws/logs`
  const ws = new WebSocket(url)

  ws.onopen = () => { connected.value = true }
  ws.onclose = () => {
    connected.value = false
    setTimeout(() => connectLog(wsRef), 3000)  // 断线重连
  }
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    logs.value.push(msg)
    if (logs.value.length > 5000) logs.value.shift()  // 限制内存
  }
  wsRef.value = ws
  return ws
}

export function clearLogs() { logs.value = [] }
export function useLogs() { return { logs, connected, clearLogs } }
