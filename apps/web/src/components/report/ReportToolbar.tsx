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

  return (
    <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
      <Button variant="secondary" size="sm" className="w-full sm:w-auto" leftIcon={<Download className="h-4 w-4" />} onClick={onDownload}>
        下载
      </Button>
      <Button variant="secondary" size="sm" className="w-full sm:w-auto" leftIcon={<Share2 className="h-4 w-4" />} onClick={onShare}>
        分享
      </Button>
      {canDeleteReport &&
        (confirmDelete ? (
          <>
            <Button
              variant="danger"
              size="sm"
              className="w-full sm:w-auto"
              leftIcon={deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              onClick={onDelete}
              disabled={deleting}
            >
              确认删除
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="w-full sm:w-auto"
              leftIcon={<X className="h-4 w-4" />}
              onClick={() => onConfirmDeleteChange(false)}
              disabled={deleting}
            >
              取消
            </Button>
          </>
        ) : (
          <Button variant="secondary" size="sm" className="w-full sm:w-auto" leftIcon={<Trash2 className="h-4 w-4" />} onClick={() => onConfirmDeleteChange(true)}>
            删除
          </Button>
        ))}
    </div>
  )
}
