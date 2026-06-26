import { Download, ExternalLink } from 'lucide-react'
import type { ArtifactsMap } from '../../lib/pdfTypes'
import { artifactRoles } from '../../lib/pdfTypes'
import { artifactDownloadName, artifactDownloadUrl, artifactUrl } from '../../lib/pdfApi'
import { handleAuthenticatedSourceClick } from '../../lib/authenticatedSourceLinks'
import { downloadAuthenticatedFile } from '../../lib/authenticatedFiles'

export interface PdfArtifactListProps {
  artifacts: ArtifactsMap
}

export function PdfArtifactList({ artifacts }: PdfArtifactListProps) {
  if (!artifacts || Object.keys(artifacts).length === 0) return null
  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <h3 className="text-base font-semibold text-text mb-3">产物文件</h3>
      <div className="text-sm text-text-muted mb-3">
        以下为本次解析生成的产物包，核心文件会共同进入 Wiki/语义抽取与 PostgreSQL 入库流程。
      </div>
      {Object.entries(artifacts).map(([name, info]) => {
        const url = artifactUrl(info)
        const downloadUrl = artifactDownloadUrl(name, info)
        return (
          <div key={name} className={`pdf-artifact-row ${info.exists ? 'ok' : 'missing'}`}>
            <div className="pdf-artifact-name">
              <span>{name}</span>
              <small>{artifactRoles[name] || '解析辅助产物'}</small>
            </div>
            <code>{info.path || '未生成'}</code>
            <div className="pdf-artifact-actions">
              {info.exists && url ? (
                <a
                  className="pdf-trace-btn inline-flex items-center gap-1"
                  href={url}
                  target="_blank"
                  rel="noopener"
                  title="打开产物"
                  onClick={(event) => {
                    handleAuthenticatedSourceClick(event.nativeEvent, url).catch((error) => {
                      console.warn('Failed to open authenticated artifact link', error)
                    })
                  }}
                >
                  <ExternalLink size={13} />
                  打开
                </a>
              ) : null}
              {info.exists && downloadUrl ? (
                <button
                  type="button"
                  className="pdf-trace-btn inline-flex items-center gap-1"
                  title={name === 'images' ? '打包下载图片' : '下载产物'}
                  onClick={() => {
                    downloadAuthenticatedFile(downloadUrl, artifactDownloadName(name)).catch((error) => {
                      console.warn('Failed to download authenticated artifact', error)
                    })
                  }}
                >
                  <Download size={13} />
                  下载
                </button>
              ) : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}
