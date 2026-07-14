import { Cloud, Cpu, ShieldCheck, TriangleAlert } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { MeetingModel } from '../types'

interface MeetingModelSelectorProps {
  models: MeetingModel[]
  value: string
  onChange: (modelRef: string) => void
  disabled?: boolean
  includeAuto?: boolean
}

export function MeetingModelSelector({
  models,
  value,
  onChange,
  disabled = false,
  includeAuto = true,
}: MeetingModelSelectorProps) {
  const selected = models.find((model) => model.model_ref === value)
  return (
    <div className="space-y-2">
      <label htmlFor="meeting-model" className="text-sm font-semibold text-text">AI 整理模型</label>
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger id="meeting-model" className="h-11 w-full min-w-0">
          <SelectValue placeholder="选择可用模型" />
        </SelectTrigger>
        <SelectContent position="popper" className="max-w-[min(32rem,calc(100vw-2rem))]">
          {includeAuto ? <SelectItem value="auto"><Cpu />自动选择可用模型</SelectItem> : null}
          {models.map((model) => (
            <SelectItem key={model.model_ref} value={model.model_ref} disabled={!model.available || !model.configured}>
              {model.locality === 'cloud' ? <Cloud /> : <Cpu />}
              <span className="min-w-0 truncate">{model.label} · {model.locality === 'cloud' ? '云端' : '本地'}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selected ? (
        <div className="flex items-start gap-2 text-xs leading-5 text-text-muted">
          {selected.locality === 'cloud' ? <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" /> : <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />}
          <span>
            {selected.locality === 'cloud'
              ? '逐字稿文本将发送至所选云端模型；会议音频和声纹不会发送。'
              : '文本在本地模型边界内处理。'}
          </span>
        </div>
      ) : null}
    </div>
  )
}
