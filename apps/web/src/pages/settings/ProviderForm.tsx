import type { ReactNode } from 'react'
import {
  BrainCircuit,
  CheckCircle2,
  Eye,
  EyeOff,
  Globe2,
  KeyRound,
  Loader2,
  PlugZap,
  SlidersHorizontal,
} from 'lucide-react'
import type { ProviderFormData, ProviderKey, ProviderMeta, TestState } from './types'

interface ProviderFormProps {
  activeKey: ProviderKey
  provider: ProviderFormData
  meta: ProviderMeta
  testState: TestState
  showKey: boolean
  onToggleShowKey: (key: ProviderKey) => void
  updateProvider: (key: ProviderKey, patch: Partial<ProviderFormData>) => void
  onTest: (key: ProviderKey) => void
}

export function ProviderForm({
  activeKey,
  provider,
  meta,
  testState,
  showKey,
  onToggleShowKey,
  updateProvider,
  onTest,
}: ProviderFormProps) {
  const ActiveIcon = meta.icon

  const modelField = (
    label: string,
    value: string,
    key: keyof ProviderFormData,
    placeholder: string,
    icon?: ReactNode,
    options?: {
      mono?: boolean
      type?: string
      min?: string
      max?: string
      step?: string
      helper?: string
    },
  ) => (
    <label className="space-y-2">
      <span className="flex items-center gap-2 text-base font-semibold text-text">
        {icon}
        {label}
      </span>
      <input
        type={options?.type || 'text'}
        min={options?.min}
        max={options?.max}
        step={options?.step}
        value={value}
        onChange={(e) => updateProvider(activeKey, { [key]: e.target.value } as Partial<ProviderFormData>)}
        placeholder={placeholder}
        className={`form-control w-full px-4 text-base ${options?.mono ? 'font-mono' : ''}`}
      />
      {options?.helper && <p className="text-sm text-text-muted">{options.helper}</p>}
    </label>
  )

  return (
    <section
      className={`apple-card overflow-hidden rounded-[var(--radius-card)] border sm:rounded-[var(--radius-panel)] ${meta.panelClass}`}
    >
      <div className="border-b border-border bg-card/80 px-4 py-4 sm:px-6 sm:py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-4">
            <div
              className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-[var(--radius-card)] sm:h-14 sm:w-14 ${meta.iconClass}`}
            >
              <ActiveIcon className="h-7 w-7" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text sm:text-lg">编辑{meta.title}</h3>
              <p className="mt-1 text-sm leading-6 text-text-muted sm:text-base sm:leading-7">
                {meta.desc}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="inline-flex h-11 cursor-pointer items-center gap-2 rounded-full border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm">
              <input
                type="checkbox"
                checked={provider.enabled}
                onChange={(e) => updateProvider(activeKey, { enabled: e.target.checked })}
                className="h-4 w-4 accent-primary"
              />
              启用
            </label>
            <button
              type="button"
              onClick={() => onTest(activeKey)}
              disabled={testState.status === 'testing'}
              className="flex h-11 items-center justify-center gap-2 rounded-full accent-gradient px-5 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:-translate-y-0.5 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-70"
            >
              {testState.status === 'testing' ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <PlugZap className="h-4 w-4" />
              )}
              测试调用
            </button>
          </div>
        </div>
      </div>

      <div className="grid gap-5 p-4 sm:gap-6 sm:p-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="space-y-5">
          <fieldset className="space-y-4 border-0 p-0">
            <legend className="text-sm font-semibold text-text-muted">Endpoint</legend>
            <div className="grid gap-5 md:grid-cols-2">
              {modelField(
                '供应商名称',
                provider.providerName,
                'providerName',
                activeKey === 'cloud' ? '例如 StepFun / Step-3.7 Flash' : '例如 vLLM / Qwen3.6、Gemma4 或 Nemotron',
                <CheckCircle2 className="h-5 w-5 text-primary" />,
              )}
              {modelField(
                'Base URL',
                provider.baseUrl,
                'baseUrl',
                activeKey === 'cloud'
                  ? 'https://api.stepfun.com/v1、hermes://minimax-cn 或 hermes://kimi-coding'
                  : 'http://127.0.0.1:8004/v1、http://127.0.0.1:8006/v1 或 http://127.0.0.1:8007/v1',
                <Globe2 className="h-5 w-5 text-primary" />,
                {
                  mono: true,
                  helper:
                    'StepFun 走 OpenAI-compatible 接口；Minimax/Kimi 的 hermes:// 预设复用 Hermes 已配置的模型与鉴权。',
                },
              )}
            </div>
          </fieldset>

          <fieldset className="space-y-4 border-0 p-0">
            <legend className="text-sm font-semibold text-text-muted">Generation</legend>
            <div className="grid gap-5 md:grid-cols-2">
              {modelField(
                '模型名称',
                provider.model,
                'model',
                activeKey === 'cloud'
                  ? '例如 step-3.7-flash、MiniMax-M3 或 kimi-for-coding'
                  : '例如 Qwen3.6-35B-A3B-FP8、Gemma-4-26B-A4B-it-NVFP4 或 nemotron_3_nano_omni',
                <BrainCircuit className="h-5 w-5 text-primary" />,
              )}
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-5 md:grid-cols-3">
              {modelField(
                'Temperature',
                provider.temperature,
                'temperature',
                '0.2',
                <SlidersHorizontal className="h-5 w-5 text-primary" />,
                { type: 'number', min: '0', max: '2', step: '0.1' },
              )}
              {modelField(
                '输出 Token 上限',
                provider.maxTokens,
                'maxTokens',
                '8192',
                undefined,
                {
                  type: 'number',
                  min: '1',
                  max: '262144',
                  helper: '这是单次生成的输出上限；模型上下文长度由 vLLM 的 max-model-len 控制。',
                },
              )}
              {modelField(
                '超时秒数',
                provider.timeoutSeconds,
                'timeoutSeconds',
                '600',
                undefined,
                { type: 'number', min: '5' },
              )}
            </div>
          </fieldset>

          <fieldset className="space-y-4 border-0 p-0">
            <legend className="text-sm font-semibold text-text-muted">Auth</legend>
            <label className="space-y-2">
              <span className="flex items-center gap-2 text-base font-semibold text-text">
                <KeyRound className="h-5 w-5 text-primary" />
                API Key
              </span>
              <div className="flex gap-2">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={provider.apiKey}
                  onChange={(e) =>
                    updateProvider(activeKey, { apiKey: e.target.value, clearApiKey: false })
                  }
                  placeholder={
                    provider.hasApiKey
                      ? '已保存密钥；留空则继续沿用'
                      : activeKey === 'cloud'
                        ? 'StepFun 需填写 API Key；Hermes 预设可留空'
                        : '本地服务如无鉴权可留空'
                  }
                  className="form-control min-w-0 flex-1 px-4 text-base"
                />
                <button
                  type="button"
                  onClick={() => onToggleShowKey(activeKey)}
                  className="flex h-[52px] w-[52px] items-center justify-center rounded-xl border border-border bg-card text-text-muted shadow-sm hover:bg-bg"
                  aria-label={showKey ? '隐藏密钥' : '显示密钥'}
                >
                  {showKey ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-sm text-text-muted">
                <span>
                  {provider.hasApiKey && !provider.clearApiKey ? '后端已保存密钥。' : '未保存密钥。'}
                </span>
                {provider.hasApiKey && (
                  <button
                    type="button"
                    onClick={() =>
                      updateProvider(activeKey, {
                        apiKey: '',
                        clearApiKey: true,
                        hasApiKey: false,
                      })
                    }
                    className="font-semibold text-red-600 hover:text-red-700"
                  >
                    清空已保存密钥
                  </button>
                )}
              </div>
            </label>
          </fieldset>
        </div>

        <aside className="space-y-4">
          <div className="rounded-[var(--radius-card)] border border-border bg-card p-4 shadow-sm sm:rounded-[var(--radius-card)] sm:p-5">
            <p className="text-sm font-semibold text-text-muted">连接状态</p>
            <p
              className={`mt-2 text-lg font-semibold ${
                testState.status === 'success'
                  ? 'text-success'
                  : testState.status === 'error'
                    ? 'text-error'
                    : 'text-text'
              }`}
            >
              {testState.status === 'success'
                ? '连接正常'
                : testState.status === 'error'
                  ? '连接失败'
                  : testState.status === 'testing'
                    ? '测试中'
                    : '尚未测试'}
            </p>
            <p className="mt-2 text-sm leading-6 text-text-muted">
              {testState.message || '点击测试调用，会用当前表单参数向模型发送一条轻量请求。'}
              {testState.latencyMs ? ` · ${testState.latencyMs}ms` : ''}
            </p>
          </div>

          <div className="rounded-[var(--radius-card)] border border-border bg-card p-5 shadow-sm">
            <p className="text-sm font-semibold text-text-muted">当前摘要</p>
            <dl className="mt-3 space-y-3 text-sm">
              <div>
                <dt className="text-text-muted">供应商</dt>
                <dd className="mt-0.5 font-semibold text-text">
                  {provider.providerName || '未填写'}
                </dd>
              </div>
              <div>
                <dt className="text-text-muted">模型</dt>
                <dd className="mt-0.5 break-all font-semibold text-text">
                  {provider.model || '未填写'}
                </dd>
              </div>
              <div>
                <dt className="text-text-muted">鉴权</dt>
                <dd className="mt-0.5 font-semibold text-text">
                  {provider.hasApiKey || provider.apiKey ? '已配置' : '未配置'}
                </dd>
              </div>
            </dl>
          </div>
        </aside>
      </div>
    </section>
  )
}
