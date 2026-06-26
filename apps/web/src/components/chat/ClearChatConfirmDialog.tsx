import { AlertTriangle } from 'lucide-react'
import { Button } from '../ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog'

interface ClearChatConfirmDialogProps {
  open: boolean
  disabled?: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void | Promise<void>
}

export default function ClearChatConfirmDialog({
  open,
  disabled = false,
  onOpenChange,
  onConfirm,
}: ClearChatConfirmDialogProps) {
  const handleConfirm = async () => {
    await onConfirm()
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="rounded-2xl border-border bg-card text-text sm:max-w-md">
        <DialogHeader>
          <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-xl bg-red-50 text-error">
            <AlertTriangle className="h-5 w-5" aria-hidden="true" />
          </div>
          <DialogTitle>删除当前会话？</DialogTitle>
          <DialogDescription>
            此操作会清空当前会话的消息记录。删除后无法从这个窗口恢复。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">
              取消
            </Button>
          </DialogClose>
          <Button type="button" variant="destructive" disabled={disabled} onClick={handleConfirm}>
            删除会话
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
