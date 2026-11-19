import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { compression } from 'vite-plugin-compression2'

// vite.config.ts 只在 Node 侧编译；本项目没有装 @types/node（不新增依赖），
// 这里只声明实际用到的两个全局，避免为配置引入额外类型包
declare const process: { env: Record<string, string | undefined> }
declare const console: { log: (...args: string[]) => void }

/**
 * 开发代理目标：默认本机 8001（当前开发后端端口）。
 * 如果后端沿用 `scripts/start-backend-dev.bat`（监听 8000），启动前端时覆盖一下即可：
 *   PowerShell: $env:VITE_API_TARGET='http://127.0.0.1:8000'; npm run dev
 *   bash:       VITE_API_TARGET=http://127.0.0.1:8000 npm run dev
 * 仅影响 dev server 的 `/api`、`/uploads` 代理，不参与生产构建（生产由 nginx 反代）。
 */
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8001'

export default defineConfig(({ command }) => {
  if (command === 'serve') {
    // 启动时打一行，排查「页面连的是哪个后端」不用再翻配置
    console.log(`[vite] /api 与 /uploads 代理目标: ${apiTarget}`)
  }

  return {
    plugins: [
      react(),
      // 产物预压缩为 .gz：配合 nginx gzip_static 直接发送预压缩文件，
      // 省去每次请求对 1.2MB 级 antd-vendor 的实时 CPU 压缩，降低 TTFB
      // （仅 gzip：镜像内 nginx 无 brotli 模块，生成 .br 属无效负载）
      compression({ algorithms: ['gz'], exclude: [/\.(png|jpe?g|webp|svg|gif)$/i] }),
    ],
    base: '/admin/',
    server: {
      // 固定 5175（--strictPort，端口占用直接报错）。
      // strictPort：端口被占时直接报错，而不是静默换到下一个端口 ——
      // 历史上正是「悄悄换端口」留下多个隐藏实例，导致页面开到旧界面。
      port: 5175,
      strictPort: true,
      // 显式绑 IPv4 回环：不写 host 时 Vite 只监听 IPv6 的 localhost，
      // 浏览器按 http://127.0.0.1:5175 打开会连不上
      host: '127.0.0.1',
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          // 站内聊天 WebSocket（/api/v1/admin/ws/chat）走同一前缀：
          // 不开 ws:true 的话升级请求会被当普通 HTTP 代理，握手直接失败
          ws: true,
        },
        // 本地存储驱动的文件由后端 /uploads 静态提供。
        // 生产环境由 nginx 反代（见 nginx.conf），开发环境靠这条 ——
        // 少了它，演示数据与后台上传的图片在本地全是 404，看起来像上传坏了。
        '/uploads': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      // 关闭构建时逐一计算 gzip 体积的耗时步骤（仅影响构建日志，不影响产物）
      reportCompressedSize: false,
      rollupOptions: {
        output: {
          // 供应商分包：业务代码迭代时不失效 vendor chunk 的浏览器缓存，
          // 三方库各自独立，可被多个懒加载页面共享与并行下载
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined
            // 框架层（react 全家桶 + router + 状态 + HTTP）：随业务迭代零变化
            if (
              id.includes('node_modules/react/') ||
              id.includes('node_modules/react-dom/') ||
              id.includes('node_modules/scheduler/') ||
              id.includes('node_modules/react-router/') ||
              id.includes('node_modules/react-router-dom/') ||
              id.includes('node_modules/zustand/') ||
              id.includes('node_modules/axios/')
            ) {
              return 'react-vendor'
            }
            // @ant-design/icons 按需拆分（每个图标独立 chunk），不归入 antd-vendor：
            // 与用户前端策略一致，按页面用到的图标随页面 chunk 加载，
            // 避免全站图标聚合进首屏必加载的 antd-vendor
            if (id.includes('node_modules/@ant-design/icons')) {
              return undefined
            }
            // antd 主包 + dayjs：全站使用，独立 chunk 便于长期缓存
            if (id.includes('node_modules/antd/') || id.includes('node_modules/dayjs/') || id.includes('node_modules/rc-') || id.includes('node_modules/@rc-component/') || id.includes('node_modules/@ant-design/')) {
              return 'antd-vendor'
            }
            // 编辑器全家桶整体归入：quill 核心 / 表格插件 / delta 模块与
            // react-quill-new 强耦合，若留在页面公共异步 chunk，任一编辑器
            // 库升级都会使该公共 chunk 缓存失效
            if (
              id.includes('node_modules/react-quill-new/') ||
              id.includes('node_modules/quill/') ||
              id.includes('node_modules/quill-table-better/') ||
              id.includes('node_modules/quill-delta/') ||
              id.includes('node_modules/marked/') ||
              id.includes('node_modules/dompurify/')
            ) {
              return 'editor-vendor'
            }
            return undefined
          },
        },
      },
    },
  }
})
