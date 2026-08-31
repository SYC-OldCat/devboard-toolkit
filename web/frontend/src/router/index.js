import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from './stores/user'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    children: [
      { path: '', redirect: '/replay' },
      { path: 'replay', name: 'replay', component: () => import('../views/Replay.vue') },
      { path: 'dataproc', name: 'dataproc', component: () => import('../views/DataProc.vue') },
      { path: 'jenkins', name: 'jenkins', component: () => import('../views/Jenkins.vue') },
      { path: 'pipeline', name: 'pipeline', component: () => import('../views/Pipeline.vue') },
      { path: 'settings', name: 'settings', component: () => import('../views/Settings.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  const userStore = useUserStore()
  if (to.name !== 'login' && !userStore.token) {
    next({ name: 'login' })
  } else {
    next()
  }
})

export default router
