import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

/**
 * 管理端 ESLint 配置（第五轮审查 MNT-02）。
 *
 * 与用户端 `frontend/eslint.config.js` 同一套取舍：**只开「一定是 bug」的规则**，
 * 不引风格争议。两端的差异只有 `ignores`（管理端多一个 `dist-verify`）与路径别名。
 *
 * 为什么管理端尤其需要：`tsconfig.json` 里 `noUnusedLocals` / `noUnusedParameters`
 * 是**关着的**（历史遗留，打开会有大量报错）。这里用 eslint 的
 * `@typescript-eslint/no-unused-vars` 作为等价兜底 —— 未使用的导入与变量会直接报错。
 *
 * 注意：`no-empty` 在本仓库有真实价值 —— 曾经有 `catch {}` 把「加载失败」
 * 静默降级成「没有数据」，用户以为数据丢了。
 */
export default tseslint.config(
  {
    ignores: [
      'dist',
      'dist-verify',
      'node_modules',
      'coverage',
      'vite.config.ts',
      'vitest.config.ts',
      'scripts/**',
    ],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/ban-ts-comment': [
        'error',
        { 'ts-expect-error': 'allow-with-description', 'ts-ignore': true },
      ],
    },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'src/test/**'],
    languageOptions: { globals: { ...globals.node } },
  },
)
