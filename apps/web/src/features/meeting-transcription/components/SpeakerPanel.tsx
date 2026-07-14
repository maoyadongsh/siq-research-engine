import { useState } from 'react'
import { Check, Fingerprint, Pencil, ShieldCheck, UserRound, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

import type { MeetingSpeakerTrack } from '../types'

interface SpeakerPanelProps {
  speakers: MeetingSpeakerTrack[]
  editable?: boolean
  voiceprintEnabled?: boolean
  onRename?: (speaker: MeetingSpeakerTrack, displayName: string, saveVoiceprint: boolean) => Promise<void>
  onMatchDecision?: (speaker: MeetingSpeakerTrack, decision: 'confirm' | 'reject' | 'undo') => Promise<void>
}

export function SpeakerPanel({
  speakers,
  editable = false,
  voiceprintEnabled = false,
  onRename,
  onMatchDecision,
}: SpeakerPanelProps) {
  const [editingId, setEditingId] = useState('')
  const [draft, setDraft] = useState('')
  const [consentSpeaker, setConsentSpeaker] = useState<MeetingSpeakerTrack | null>(null)
  const [consentAccepted, setConsentAccepted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function rename(speaker: MeetingSpeakerTrack, saveVoiceprint: boolean) {
    if (!onRename || !draft.trim()) return
    setBusy(true)
    setError('')
    try {
      await onRename(speaker, draft.trim(), saveVoiceprint)
      setEditingId('')
      setConsentSpeaker(null)
      setConsentAccepted(false)
    } catch (renameError) {
      setError(renameError instanceof Error ? renameError.message : '发言人命名失败')
    } finally {
      setBusy(false)
    }
  }

  if (!speakers.length) {
    return <p className="py-8 text-center text-sm leading-6 text-text-muted">检测到稳定发言后，匿名发言人会显示在这里。</p>
  }

  return (
    <>
      <div className="divide-y divide-border/70">
        {speakers.map((speaker) => {
          const suggestion = speaker.voiceprint_match?.decision === 'suggested' ? speaker.voiceprint_match : null
          return (
            <div key={speaker.id} className="py-4 first:pt-0 last:pb-0">
              <div className="flex items-start gap-3">
                <span className="premium-icon h-10 w-10 shrink-0 rounded-md"><UserRound className="h-5 w-5" /></span>
                <div className="min-w-0 flex-1">
                  {editingId === speaker.id ? (
                    <div className="space-y-2">
                      <label className="sr-only" htmlFor={`speaker-${speaker.id}`}>发言人姓名</label>
                      <Input id={`speaker-${speaker.id}`} value={draft} onChange={(event) => setDraft(event.target.value)} autoFocus />
                      <div className="flex flex-wrap gap-2">
                        <Button type="button" size="sm" className="max-sm:h-11" onClick={() => void rename(speaker, false)} disabled={busy || !draft.trim()}><Check />本场全部</Button>
                        {voiceprintEnabled ? (
                          <Button type="button" size="sm" variant="secondary" className="max-sm:h-11" onClick={() => setConsentSpeaker(speaker)} disabled={busy || !draft.trim()}><Fingerprint />保存声纹</Button>
                        ) : null}
                        <Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => setEditingId('')} aria-label="取消命名"><X /></Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex min-w-0 items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-text">{speaker.display_name || speaker.anonymous_label}</p>
                        <p className="mt-0.5 text-xs text-text-muted">
                          {speaker.label_source === 'manual' ? '本场人工确认' : speaker.label_source === 'voiceprint_confirmed' ? '声纹已确认' : speaker.label_source === 'voiceprint_auto' ? '声纹自动识别' : speaker.anonymous_label}
                          {speaker.match_confidence != null ? ` · ${Math.round(speaker.match_confidence * 100)}%` : ''}
                        </p>
                      </div>
                      {editable ? (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => { setEditingId(speaker.id); setDraft(speaker.display_name || speaker.anonymous_label); setError('') }} aria-label="命名发言人"><Pencil /></Button>
                            </TooltipTrigger>
                            <TooltipContent>命名发言人</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : null}
                    </div>
                  )}
                  {suggestion ? (
                    <div className="mt-3 rounded-md border border-warning/25 bg-warning-soft/55 p-3">
                      <p className="text-xs leading-5 text-text">可能是 <strong>{suggestion.display_name || '已授权身份'}</strong>{suggestion.confidence != null ? ` · ${Math.round(suggestion.confidence * 100)}%` : ''}</p>
                      <div className="mt-2 flex gap-2">
                        <Button type="button" size="sm" className="max-sm:h-11" onClick={() => void onMatchDecision?.(speaker, 'confirm')}><Check />确认</Button>
                        <Button type="button" size="sm" variant="ghost" className="max-sm:h-11" onClick={() => void onMatchDecision?.(speaker, 'reject')}><X />不是</Button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          )
        })}
        {error ? <p role="alert" className="pt-3 text-sm text-error">{error}</p> : null}
      </div>

      <Dialog open={Boolean(consentSpeaker)} onOpenChange={(open) => { if (!open && !busy) setConsentSpeaker(null) }}>
        <DialogContent className="bg-card text-text sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-primary" />授权保存声纹</DialogTitle>
            <DialogDescription className="leading-6">
              声纹仅用于你未来会议中的发言人识别。系统保存加密后的特征模板，不在页面展示向量，也不会发送给 Hermes 云端模型。
            </DialogDescription>
          </DialogHeader>
          <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm leading-6">
            <input type="checkbox" checked={consentAccepted} onChange={(event) => setConsentAccepted(event.target.checked)} className="mt-1" />
            我已了解用途、个人私有范围和撤销方式，并同意使用本场清晰语音片段注册声纹。
          </label>
          {error ? <p role="alert" className="text-sm text-error">{error}</p> : null}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setConsentSpeaker(null)} disabled={busy}>取消</Button>
            <Button type="button" onClick={() => consentSpeaker && void rename(consentSpeaker, true)} disabled={!consentAccepted || busy}><Fingerprint />{busy ? '保存中' : '同意并保存'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
