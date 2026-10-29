import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/**
 * 测试专用配置（与 `vite.config.ts` 分离）。
 *
 * 为什么不合并到 `vite.config.ts`：那份配置带 `base: '/admin/'` 与产物压缩插件，
 * 都是**构建期**的关注点；测试不需要它们，混在一起会让「改构建配置」有概率弄坏测试，
 * 反之亦然。分开维护，各自演进。
 *
 * 环境用 jsdom：QuillEditor 依赖 DOM。注意 jsdom 对 `contenteditable` / `Range` 的
 * 支持有限，故测试只覆盖**纯逻辑**与**组件能否挂载**，不去断言 Quill 的内核行为
 * （那类断言在 jsdom 下会得到假结果）。
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 组件测试会拉入 antd / quill 等重依赖，放宽单条用例超时
    testTimeout: 20000,
  },
})
