import { type CSSProperties } from 'react'
import { Download, Loader2, Share2, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui'

interface ReportToolbarProps {
  selectedReportUrl: string
  canDeleteReport: boolean
  confirmDelete: boolean
  deleting: boolean
  onConfirmDeleteChange: (confirm: boolean) => void
  onShare: () => void
  onDownload: () => void
  onDelete: () => void
}

export default function ReportToolbar({
  selectedReportUrl,
  canDeleteReport,
  confirmDelete,
  deleting,
  onConfirmDeleteChange,
  onShare,
  onDownload,
  onDelete,
}: ReportToolbarProps) {
  if (!selectedReportUrl) return null

  const actionCount = confirmDelete ? 4 : canDeleteReport ? 3 : 2
  const toolbarStyle = { gridTemplateColumns: `repeat(${actionCount}, minmax(0, 1fr))` } as CSSProperties

  return (
    <div className="grid gap-2 sm:flex sm:flex-wrap" style={toolbarStyle}>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="h-11 w-full min-w-0 px-2 sm:h-8 sm:w-auto sm:px-3"
        leftIcon={<Download className="h-4 w-4" />}
        onClick={onDownload}
      >
        下载
      </Button>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="h-11 w-full min-w-0 px-2 sm:h-8 sm:w-auto sm:px-3"
        leftIcon={<Share2 className="h-4 w-4" />}
        onClick={onShare}
      >
        分享
      </Button>
      {canDeleteReport &&
        (confirmDelete ? (
          <>
            <Button
              type="button"
              variant="danger"
              size="sm"
              className="h-11 w-full min-w-0 px-2 sm:h-8 sm:w-auto sm:px-3"
              leftIcon={deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              onClick={onDelete}
              disabled={deleting}
            >
              确认删除
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="h-11 w-full min-w-0 px-2 sm:h-8 sm:w-auto sm:px-3"
              leftIcon={<X className="h-4 w-4" />}
              onClick={() => onConfirmDeleteChange(false)}
              disabled={deleting}
            >
              取消
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="h-11 w-full min-w-0 px-2 sm:h-8 sm:w-auto sm:px-3"
            leftIcon={<Trash2 className="h-4 w-4" />}
            onClick={() => onConfirmDeleteChange(true)}
          >
            删除
          </Button>
        ))}
    </div>
  )
}
