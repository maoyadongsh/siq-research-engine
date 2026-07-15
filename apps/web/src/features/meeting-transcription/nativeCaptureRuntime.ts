import {
  IosNativeCaptureAdapter,
  selectMeetingCaptureAdapter,
  type MeetingCaptureCapabilityEnvelope,
  type MeetingCaptureSelectionReason,
} from './captureAdapter'
import {
  MEETING_CAPTURE_PLUGIN_NAME,
  probeMeetingNativeRuntime,
  type CapacitorRuntimeLike,
  type MeetingCapturePluginBridge,
} from './nativeCapture'

interface CapacitorModule {
  Capacitor: CapacitorRuntimeLike
  registerPlugin<T>(name: string): T
}

export interface NativeCaptureRuntimeSelection {
  adapter: IosNativeCaptureAdapter | null
  reason: MeetingCaptureSelectionReason
}

export function nativeCaptureFrontendEnabled() {
  const buildValue = import.meta.env.VITE_SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED
  const runtime = (globalThis as typeof globalThis & {
    __SIQ_CONFIG__?: Record<string, unknown>
  }).__SIQ_CONFIG__
  const runtimeValue = runtime?.SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED
    ?? runtime?.VITE_SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED
  return ['1', 'true', 'yes', 'on'].includes(String(runtimeValue ?? buildValue ?? '').toLowerCase())
}

export async function loadNativeCaptureRuntime(
  capabilities: MeetingCaptureCapabilityEnvelope | null,
  options: {
    frontendEnabled?: boolean
    loadCapacitor?: () => Promise<CapacitorModule>
  } = {},
): Promise<NativeCaptureRuntimeSelection> {
  const frontendEnabled = options.frontendEnabled ?? nativeCaptureFrontendEnabled()
  if (!frontendEnabled) return { adapter: null, reason: 'native_frontend_flag_disabled' }
  const capacitor = await (options.loadCapacitor?.() ?? import('@capacitor/core'))
  const decision = selectMeetingCaptureAdapter({
    nativeFeatureEnabled: true,
    runtime: probeMeetingNativeRuntime(capacitor.Capacitor),
    capabilities,
  })
  if (decision.adapter !== 'ios_native') return { adapter: null, reason: decision.reason }
  const plugin = capacitor.registerPlugin<MeetingCapturePluginBridge>(MEETING_CAPTURE_PLUGIN_NAME)
  return { adapter: new IosNativeCaptureAdapter(plugin), reason: decision.reason }
}
