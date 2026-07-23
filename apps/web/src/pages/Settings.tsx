import { useCallback, useEffect, useMemo, useState } from 'react'
import { BrainCircuit, CheckCircle2, Cloud, Cpu, Loader2, RotateCcw, Save, SlidersHorizontal, Sparkles } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { PageHeader, PageShell } from '@/components/page'
import { fetchLlmSettings, fetchSystemStatus, saveLlmSettings, testLlmProvider, type LlmProviderPayload } from '@/features/settings/api'
import { ConnectionSection } from './settings/ConnectionSection'
import { ProviderCard } from './settings/ProviderCard'
import { ProviderForm } from './settings/ProviderForm'
import { SystemStatusSection } from './settings/SystemStatusSection'
import { countEnabledServices, mapProviderFromApi, normalizeNumber, readSetting } from './settings/utils'
import type { LLMSettingsForm, ProviderFormData, ProviderKey, SystemStatus, TestState } from './settings/types'

const DEFAULTS = { apiBase: '', wikiRoot: 'data/wiki', recentLimit: '8' }
const LOCAL_GEMMA4_PRESET: ProviderFormData = { enabled: true, providerName: '本地 vLLM / Gemma4', baseUrl: 'http://127.0.0.1:8006/v1', apiKey: '', hasApiKey: false, clearApiKey: true, model: 'Gemma-4-26B-A4B-it-NVFP4', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '600' }
const LOCAL_QWEN_PRESET: ProviderFormData = { enabled: true, providerName: '本地 vLLM / Qwen3.6', baseUrl: 'http://127.0.0.1:8004/v1', apiKey: '', hasApiKey: false, clearApiKey: true, model: 'Qwen3.6-35B-A3B-FP8', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '180' }
const LOCAL_NEMOTRON_PRESET: ProviderFormData = { enabled: true, providerName: '本地 vLLM / Nemotron 3 Nano Omni', baseUrl: 'http://127.0.0.1:8007/v1', apiKey: '', hasApiKey: false, clearApiKey: true, model: 'nemotron_3_nano_omni', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '600' }
const CLOUD_STEPFUN_PRESET: ProviderFormData = { enabled: true, providerName: 'StepFun / Step-3.7 Flash', baseUrl: 'https://api.stepfun.com/v1', apiKey: '', hasApiKey: false, clearApiKey: false, model: 'step-3.7-flash', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '180' }
const CLOUD_MINIMAX_PRESET: ProviderFormData = { enabled: true, providerName: 'Hermes / Minimax', baseUrl: 'hermes://minimax-cn', apiKey: '', hasApiKey: false, clearApiKey: true, model: 'MiniMax-M3', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '180' }
const CLOUD_KIMI_PRESET: ProviderFormData = { enabled: true, providerName: 'Hermes / Kimi', baseUrl: 'hermes://kimi-coding', apiKey: '', hasApiKey: false, clearApiKey: true, model: 'kimi-for-coding', temperature: '0.2', maxTokens: '8192', timeoutSeconds: '180' }
const DEFAULT_LLM_SETTINGS: LLMSettingsForm = { activeProvider: 'local', providers: { cloud: CLOUD_MINIMAX_PRESET, local: LOCAL_QWEN_PRESET } }

export default function Settings() {
  const { apiUrl } = useApi()
  const [apiBase, setApiBase] = useState(() => readSetting('api_base', DEFAULTS.apiBase)); const [wikiRoot, setWikiRoot] = useState(() => readSetting('wiki_root_hint', DEFAULTS.wikiRoot)); const [recentLimit, setRecentLimit] = useState(() => readSetting('recent_task_limit', DEFAULTS.recentLimit))
  const [llmSettings, setLlmSettings] = useState<LLMSettingsForm>(DEFAULT_LLM_SETTINGS); const [saved, setSaved] = useState(false); const [loadingLlm, setLoadingLlm] = useState(true); const [savingLlm, setSavingLlm] = useState(false); const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null); const [loadingSystemStatus, setLoadingSystemStatus] = useState(true); const [systemStatusError, setSystemStatusError] = useState(''); const [showKeys, setShowKeys] = useState<Record<ProviderKey, boolean>>({ cloud: false, local: false }); const [testState, setTestState] = useState<Record<ProviderKey, TestState>>({ cloud: { status: 'idle', message: '' }, local: { status: 'idle', message: '' } })
  const [stepfunPreset, setStepfunPreset] = useState(CLOUD_STEPFUN_PRESET)

  useEffect(() => { if (!saved) return; const timer = window.setTimeout(() => setSaved(false), 1800); return () => window.clearTimeout(timer) }, [saved])
  useEffect(() => { localStorage.removeItem('pdf_api_base') }, [])
  useEffect(() => {
    let ignore = false
    async function load() {
      setLoadingLlm(true)
      try {
        const data = await fetchLlmSettings(apiUrl)
        if (ignore) return
        setLlmSettings({
          activeProvider: data.activeProvider === 'cloud' ? 'cloud' : 'local',
          providers: {
            cloud: mapProviderFromApi(data.providers?.cloud || {}, DEFAULT_LLM_SETTINGS.providers.cloud),
            local: mapProviderFromApi(data.providers?.local || {}, DEFAULT_LLM_SETTINGS.providers.local),
          },
        })
        setStepfunPreset(mapProviderFromApi(data.cloudModelPresets?.stepfun || {}, CLOUD_STEPFUN_PRESET))
      } catch (e) {
        if (!ignore) setTestState((c) => ({ ...c, local: { status: 'error', message: `无法加载后端模型配置：${e instanceof Error ? e.message : '未知错误'}` } }))
      } finally {
        if (!ignore) setLoadingLlm(false)
      }
    }
    load()
    return () => {
      ignore = true
    }
  }, [apiUrl])
  const loadSystemStatus = useCallback(async () => {
    setLoadingSystemStatus(true)
    setSystemStatusError('')
    try {
      setSystemStatus(await fetchSystemStatus(apiUrl))
    } catch (e) {
      setSystemStatus(null)
      setSystemStatusError(e instanceof Error ? e.message : '无法获取系统状态')
    } finally {
      setLoadingSystemStatus(false)
    }
  }, [apiUrl])
  useEffect(() => { loadSystemStatus() }, [apiUrl, loadSystemStatus])

  const activeProviderMeta = useMemo(() => ({ label: llmSettings.activeProvider === 'cloud' ? '云端大模型' : '本地大模型', provider: llmSettings.providers[llmSettings.activeProvider] }), [llmSettings])
  const updateProvider = (key: ProviderKey, patch: Partial<ProviderFormData>) => setLlmSettings((current) => ({ ...current, providers: { ...current.providers, [key]: { ...current.providers[key], ...patch } } }))
  const toProviderPayload = (provider: ProviderFormData): LlmProviderPayload => ({ enabled: provider.enabled, providerName: provider.providerName.trim(), baseUrl: provider.baseUrl.trim(), apiKey: provider.apiKey.trim() || null, clearApiKey: provider.clearApiKey, model: provider.model.trim(), temperature: normalizeNumber(provider.temperature, 0.2), maxTokens: Math.round(normalizeNumber(provider.maxTokens, 4096)), timeoutSeconds: Math.round(normalizeNumber(provider.timeoutSeconds, 60)) })
  const saveBaseSettings = () => { localStorage.setItem('api_base', apiBase.trim()); localStorage.removeItem('pdf_api_base'); localStorage.setItem('wiki_root_hint', wikiRoot.trim() || DEFAULTS.wikiRoot); localStorage.setItem('recent_task_limit', recentLimit.trim() || DEFAULTS.recentLimit) }
  const useLocalPreset = (preset: ProviderFormData, label: string) => { setLlmSettings((current) => ({ ...current, activeProvider: 'local', providers: { ...current.providers, local: { ...preset } } })); setSystemStatus((current) => current ? ({ ...current, model: { ...current.model, activeProvider: 'local', activeProviderName: preset.providerName, activeModel: preset.model, activeBaseUrl: preset.baseUrl } }) : current); setTestState((current) => ({ ...current, local: { status: 'idle', message: `已填入${label}，可直接保存或测试调用。` } })) }
  const useCloudPreset = (preset: ProviderFormData, label: string) => { setLlmSettings((current) => ({ ...current, activeProvider: 'cloud', providers: { ...current.providers, cloud: { ...preset } } })); setSystemStatus((current) => current ? ({ ...current, model: { ...current.model, activeProvider: 'cloud', activeProviderName: preset.providerName, activeModel: preset.model, activeBaseUrl: preset.baseUrl } }) : current); setTestState((current) => ({ ...current, cloud: { status: 'idle', message: `已填入${label}，保存后会同步到 Hermes 智能体。` } })) }
  const useLocalQwenPreset = () => useLocalPreset(LOCAL_QWEN_PRESET, '本机 vLLM Qwen3.6')
  const useLocalGemmaPreset = () => useLocalPreset(LOCAL_GEMMA4_PRESET, '本机 vLLM Gemma4')
  const useLocalNemotronPreset = () => useLocalPreset(LOCAL_NEMOTRON_PRESET, '本机 vLLM Nemotron 3 Nano Omni')
  const useCloudStepfunPreset = () => useCloudPreset(stepfunPreset, 'StepFun Step-3.7 Flash')
  const useCloudMinimaxPreset = () => useCloudPreset(CLOUD_MINIMAX_PRESET, 'Hermes Minimax')
  const useCloudKimiPreset = () => useCloudPreset(CLOUD_KIMI_PRESET, 'Hermes Kimi')
  const save = async () => {
    saveBaseSettings()
    setSavingLlm(true)
    try {
      const data = await saveLlmSettings(apiUrl, { activeProvider: llmSettings.activeProvider, providers: { cloud: toProviderPayload(llmSettings.providers.cloud), local: toProviderPayload(llmSettings.providers.local) } })
      const nextActiveProvider: ProviderKey = data.activeProvider === 'cloud' ? 'cloud' : 'local'
      const nextProviders = { cloud: mapProviderFromApi(data.providers?.cloud || {}, llmSettings.providers.cloud), local: mapProviderFromApi(data.providers?.local || {}, llmSettings.providers.local) }
      const nextProvider = nextProviders[nextActiveProvider]
      setLlmSettings({ activeProvider: nextActiveProvider, providers: nextProviders })
      setSystemStatus((current) => current ? ({ ...current, model: { ...current.model, activeProvider: nextActiveProvider, activeProviderName: nextProvider.providerName, activeModel: nextProvider.model, activeBaseUrl: nextProvider.baseUrl } }) : current)
      setSaved(true)
    } catch (e) {
      setTestState((c) => ({ ...c, [llmSettings.activeProvider]: { status: 'error', message: `保存模型配置失败：${e instanceof Error ? e.message : '未知错误'}` } }))
    } finally {
      setSavingLlm(false)
    }
  }
  const reset = () => { setApiBase(DEFAULTS.apiBase); setWikiRoot(DEFAULTS.wikiRoot); setRecentLimit(DEFAULTS.recentLimit); setLlmSettings(DEFAULT_LLM_SETTINGS); setStepfunPreset(CLOUD_STEPFUN_PRESET); localStorage.removeItem('api_base'); localStorage.removeItem('pdf_api_base'); localStorage.removeItem('wiki_root_hint'); localStorage.removeItem('recent_task_limit'); setSaved(true) }
  const testProvider = async (key: ProviderKey) => { const provider = llmSettings.providers[key]; setTestState((c) => ({ ...c, [key]: { status: 'testing', message: '正在调用模型...' } })); try { const data = await testLlmProvider(apiUrl, { provider: key, message: '请只回复 OK，用于 SIQ 连接测试。', config: toProviderPayload(provider) }); setTestState((c) => ({ ...c, [key]: { status: data.ok ? 'success' : 'error', message: data.message || (data.ok ? '连接成功' : '连接失败'), latencyMs: data.latencyMs } })) } catch (e) { setTestState((c) => ({ ...c, [key]: { status: 'error', message: e instanceof Error ? e.message : '连接测试失败' } })) } }

  const selectProvider = (key: ProviderKey) => setLlmSettings((c) => ({ ...c, activeProvider: key }))
  const toggleShowKey = (key: ProviderKey) => setShowKeys((c) => ({ ...c, [key]: !c[key] }))

  const providerMeta = { local: { title: '本地大模型', desc: '在本机 vLLM 的 Qwen3.6、Gemma4 与 Nemotron 之间切换。', icon: Cpu, iconClass: 'bg-success/10 text-success', panelClass: 'border-primary/20 bg-card' }, cloud: { title: '云端大模型', desc: '在 StepFun、Minimax 与 Kimi 之间切换；Hermes profiles 会保留本地模型备用链。', icon: Cloud, iconClass: 'bg-primary/10 text-primary', panelClass: 'border-primary/20 bg-card' } } as const
  const activeProvider = llmSettings.providers[llmSettings.activeProvider]; const activeMeta = providerMeta[llmSettings.activeProvider]; const activeTest = testState[llmSettings.activeProvider]
  const counts = useMemo(() => countEnabledServices(systemStatus?.services || []), [systemStatus])

  return (
    <PageShell className="space-y-5 sm:space-y-7">
      <PageHeader icon={SlidersHorizontal} eyebrow="Settings" title="SIQ 设置" description="管理服务连接、大模型调用、Wiki 数据位置和界面偏好。密钥仅保存在本机后端配置中。" actions={
        <div className="flex flex-wrap gap-3">
          <button onClick={reset} className="flex h-11 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><RotateCcw className="h-4 w-4" />恢复默认</button>
          <button onClick={save} disabled={savingLlm} className="flex h-11 items-center gap-2 rounded-xl accent-gradient px-4 text-sm font-semibold text-white shadow-lg shadow-blue-900/15 transition-all hover:-translate-y-0.5 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-70">{savingLlm ? <Loader2 className="h-4 w-4 animate-spin" /> : saved ? <CheckCircle2 className="h-4 w-4" /> : <Save className="h-4 w-4" />}{savingLlm ? '保存中' : saved ? '已保存' : '保存设置'}</button>
        </div>
      } />
      <ConnectionSection apiBase={apiBase} setApiBase={setApiBase} wikiRoot={wikiRoot} setWikiRoot={setWikiRoot} recentLimit={recentLimit} setRecentLimit={setRecentLimit} loadingLlm={loadingLlm} activeProviderMeta={activeProviderMeta} />
      <SystemStatusSection systemStatus={systemStatus} loadingSystemStatus={loadingSystemStatus} systemStatusError={systemStatusError} loadSystemStatus={loadSystemStatus} counts={counts} activeProvider={activeProvider} />
      <section className="space-y-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <h2 className="text-base font-semibold text-text sm:text-lg">模型服务配置</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-muted sm:text-base sm:leading-7">选择本地或云端作为当前调用源。保存后会同步到 SIQ Hermes profiles；本地模型可在 Qwen3.6、Gemma4 与 Nemotron 之间切换。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={useLocalQwenPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Cpu className="h-4 w-4 text-success" />使用本机 Qwen3.6</button>
            <button type="button" onClick={useLocalGemmaPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Sparkles className="h-4 w-4 text-primary" />使用本机 Gemma4</button>
            <button type="button" onClick={useLocalNemotronPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><BrainCircuit className="h-4 w-4 text-primary" />使用本机 Nemotron</button>
            <button type="button" onClick={useCloudStepfunPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Cloud className="h-4 w-4 text-primary" />使用 StepFun</button>
            <button type="button" onClick={useCloudMinimaxPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Cloud className="h-4 w-4 text-primary" />使用 Minimax</button>
            <button type="button" onClick={useCloudKimiPreset} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-4 text-sm font-semibold text-text shadow-sm hover:bg-bg"><Cloud className="h-4 w-4 text-primary" />使用 Kimi</button>
            <div className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-sm font-semibold text-text shadow-sm"><span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />当前：{activeMeta.title}</div>
          </div>
        </div>
        <div className="grid items-stretch gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="grid gap-5 xl:grid-rows-2">{(['local','cloud'] as ProviderKey[]).map((key) => <ProviderCard key={key} providerKey={key} provider={llmSettings.providers[key]} meta={providerMeta[key]} state={testState[key]} isActive={llmSettings.activeProvider === key} onSelect={selectProvider} />)}</div>
          <ProviderForm activeKey={llmSettings.activeProvider} provider={activeProvider} meta={activeMeta} testState={activeTest} showKey={showKeys[llmSettings.activeProvider]} onToggleShowKey={toggleShowKey} updateProvider={updateProvider} onTest={testProvider} />
        </div>
      </section>
    </PageShell>
  )
}
