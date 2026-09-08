import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue') },
    { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue') },
    { path: '/runs/:run(.*)/paper/:paper', name: 'paper', component: () => import('./views/PaperView.vue'), props: true },
    { path: '/compare', name: 'compare', component: () => import('./views/CompareView.vue') },
    { path: '/benchmark', name: 'benchmark', component: () => import('./views/BenchmarkView.vue') },
    { path: '/network', name: 'network', component: () => import('./views/NetworkView.vue') },
    { path: '/network/runs/:runId', name: 'network-run', component: () => import('./views/NetworkRunView.vue'), props: true },
    { path: '/skill', name: 'skill', component: () => import('./views/SkillView.vue') },
    { path: '/agenda', name: 'agenda', component: () => import('./views/AgendaView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
