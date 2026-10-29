import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { compression } from 'vite-plugin-compression2'

export default defineConfig({
  plugins: [
    react(),
    // 产物预压缩为 .gz：配合 nginx gzip_static 直接发送预压缩文件，
    // 省去每次请求对 1.2MB 级 antd-vendor 的实时 CPU 压缩，降低 TTFB
    // （仅 gzip：镜像内 nginx 无 brotli 模块，生成 .br 属无效负载）
    compression({ algorithms: ['gz'], exclude: [/\.(png|jpe?g|webp|svg|gif)$/i] }),
  ],
  base: '/admin/',
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
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
})
