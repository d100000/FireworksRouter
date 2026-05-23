import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// 后端开发地址可通过环境变量覆盖：
//   VITE_BACKEND=http://127.0.0.1:9000 npm run dev
// 默认指向 docker-compose / 本机后端的 8011 端口。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_BACKEND || 'http://127.0.0.1:8011'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src')
      }
    },
    server: {
      host: '127.0.0.1',
      port: Number(env.VITE_PORT) || 5173,
      proxy: {
        '/api': backendUrl,
        '/admin': backendUrl,
        '/v1': backendUrl,
        '/system': backendUrl,
        '/healthz': backendUrl,
        '/readyz': backendUrl,
        '/docs': backendUrl,
        '/openapi.json': backendUrl,
      }
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true
    }
  }
})
