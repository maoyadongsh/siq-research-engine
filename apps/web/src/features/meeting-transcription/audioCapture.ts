const workletSource = String.raw`
class SiqMeetingPcmProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const config = options.processorOptions || {}
    this.targetRate = config.targetSampleRate || 16000
    this.chunkMs = config.chunkMs || 500
    this.outputFrames = Math.round(this.targetRate * this.chunkMs / 1000)
    this.ratio = sampleRate / this.targetRate
    this.samples = []
    this.position = 0
    this.levelEnergy = 0
    this.levelSampleCount = 0
    this.lastLevel = 0
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0]
    if (!input || input.length === 0) return true
    let sum = 0
    for (let index = 0; index < input.length; index += 1) {
      const value = input[index]
      this.samples.push(value)
      sum += value * value
    }
    this.levelEnergy += sum
    this.levelSampleCount += input.length
    if (this.levelSampleCount >= sampleRate / 10) {
      this.lastLevel = Math.min(1, Math.sqrt(this.levelEnergy / this.levelSampleCount) * 4)
      this.port.postMessage({ type: 'level', level: this.lastLevel })
      this.levelEnergy = 0
      this.levelSampleCount = 0
    }
    const needed = this.position + (this.outputFrames - 1) * this.ratio + 1
    while (this.samples.length > needed) {
      const output = new Int16Array(this.outputFrames)
      for (let index = 0; index < this.outputFrames; index += 1) {
        const sourcePosition = this.position + index * this.ratio
        const left = Math.floor(sourcePosition)
        const fraction = sourcePosition - left
        const sample = this.samples[left] * (1 - fraction) + this.samples[left + 1] * fraction
        const clamped = Math.max(-1, Math.min(1, sample))
        output[index] = clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767)
      }
      const nextPosition = this.position + this.outputFrames * this.ratio
      const consumed = Math.floor(nextPosition)
      this.samples.splice(0, consumed)
      this.position = nextPosition - consumed
      this.port.postMessage({ type: 'chunk', pcm: output.buffer, level: this.lastLevel }, [output.buffer])
    }
    return true
  }
}

registerProcessor('siq-meeting-pcm-processor', SiqMeetingPcmProcessor)
`

export interface MeetingAudioCaptureOptions {
  deviceId?: string
  source?: 'microphone' | 'tab' | 'system' | string
  chunkMs?: number
  onChunk: (pcm: ArrayBuffer, capturedAt: number) => void
  onLevel?: (level: number) => void
}

export interface MeetingAudioCapture {
  start(): Promise<void>
  pause(): Promise<void>
  resume(): Promise<void>
  recover?(): Promise<void>
  stop(): Promise<void>
}

export function describeMeetingMicrophoneError(cause: unknown): Error {
  const name = cause instanceof DOMException
    ? cause.name
    : cause && typeof cause === 'object' && 'name' in cause
      ? String((cause as { name?: unknown }).name || '')
      : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return new Error('浏览器未允许麦克风权限。请在地址栏的网站设置中允许麦克风，然后重新点击开始会议。')
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return new Error('未检测到可用麦克风。请确认系统已识别输入设备。')
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return new Error('麦克风当前无法读取，可能正被其他应用独占。请关闭占用麦克风的应用后重试。')
  }
  if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
    return new Error('此前选择的麦克风已不可用，请返回新建会议页面重新选择输入设备。')
  }
  if (name === 'AbortError') {
    return new Error('麦克风请求被浏览器取消，请重新点击开始会议。')
  }
  return cause instanceof Error && cause.message
    ? new Error(`无法启动麦克风：${cause.message}`)
    : new Error('无法启动麦克风，请检查浏览器权限和系统输入设备后重试。')
}

export class AudioWorkletMeetingCapture implements MeetingAudioCapture {
  private readonly options: MeetingAudioCaptureOptions
  private context: AudioContext | null = null
  private stream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private processor: AudioWorkletNode | null = null
  private silentGain: GainNode | null = null
  private moduleUrl = ''

  constructor(options: MeetingAudioCaptureOptions) {
    this.options = options
  }

  async start() {
    if (this.context) return
    if (window.isSecureContext === false) {
      throw new Error('麦克风只能在 HTTPS 或本机安全页面中使用。')
    }
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器不支持麦克风采集')
    const AudioContextConstructor = window.AudioContext
    if (!AudioContextConstructor || typeof AudioWorkletNode === 'undefined') {
      throw new Error('当前浏览器不支持会议实时转写所需的 AudioWorklet')
    }

    try {
      if (this.options.source && this.options.source !== 'microphone') {
        if (!navigator.mediaDevices.getDisplayMedia) throw new Error('当前浏览器不支持标签页或系统音频采集')
        this.stream = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true })
        this.stream.getVideoTracks().forEach((track) => track.stop())
        if (!this.stream.getAudioTracks().length) throw new Error('未选择可共享的标签页或系统音频')
      } else {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: this.options.deviceId ? { exact: this.options.deviceId } : undefined,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
      }
      try {
        this.context = new AudioContextConstructor({ latencyHint: 'interactive', sampleRate: 16000 })
      } catch {
        this.context = new AudioContextConstructor({ latencyHint: 'interactive' })
      }
      this.moduleUrl = URL.createObjectURL(new Blob([workletSource], { type: 'text/javascript' }))
      await this.context.audioWorklet.addModule(this.moduleUrl)
      this.source = this.context.createMediaStreamSource(this.stream)
      this.processor = new AudioWorkletNode(this.context, 'siq-meeting-pcm-processor', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { targetSampleRate: 16000, chunkMs: this.options.chunkMs || 500 },
      })
      this.silentGain = this.context.createGain()
      this.silentGain.gain.value = 0
      this.processor.port.onmessage = (event: MessageEvent<{ type: string; pcm?: ArrayBuffer; level?: number }>) => {
        if (event.data.type === 'chunk' && event.data.pcm) this.options.onChunk(event.data.pcm, performance.now())
        if (event.data.level != null) this.options.onLevel?.(event.data.level)
      }
      this.source.connect(this.processor)
      this.processor.connect(this.silentGain)
      this.silentGain.connect(this.context.destination)
      await this.context.resume()
    } catch (cause) {
      await this.stop()
      throw describeMeetingMicrophoneError(cause)
    }
  }

  async pause() {
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = false })
    if (this.context?.state === 'running') await this.context.suspend()
  }

  async resume() {
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = true })
    if (this.context && this.context.state !== 'running' && this.context.state !== 'closed') {
      await this.context.resume()
    }
  }

  async recover() {
    const track = this.stream?.getAudioTracks()[0]
    const captureEnded = !track || track.readyState === 'ended'
    const contextEnded = !this.context || this.context.state === 'closed'
    if (captureEnded || contextEnded) {
      await this.stop()
      await this.start()
      return
    }
    try {
      await this.resume()
    } catch {
      await this.stop()
      await this.start()
    }
  }

  async stop() {
    this.processor?.disconnect()
    this.source?.disconnect()
    this.silentGain?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    if (this.context && this.context.state !== 'closed') await this.context.close()
    if (this.moduleUrl) URL.revokeObjectURL(this.moduleUrl)
    this.context = null
    this.stream = null
    this.source = null
    this.processor = null
    this.silentGain = null
    this.moduleUrl = ''
  }
}

export function createMeetingAudioCapture(options: MeetingAudioCaptureOptions): MeetingAudioCapture {
  return new AudioWorkletMeetingCapture(options)
}
