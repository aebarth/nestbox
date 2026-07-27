import { Minus, Plus, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

// PDF.js canvas rendering: works in every browser, no reliance on the
// built-in PDF plugin (which some browsers disable inside iframes).
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

function Page({ pdf, pageNumber, scale }) {
  const canvasRef = useRef(null)
  useEffect(() => {
    let cancelled = false
    let renderTask = null
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled) return
      const viewport = page.getViewport({ scale })
      const canvas = canvasRef.current
      if (!canvas) return
      const ratio = window.devicePixelRatio || 1
      canvas.width = viewport.width * ratio
      canvas.height = viewport.height * ratio
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      const ctx = canvas.getContext('2d')
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      renderTask = page.render({ canvasContext: ctx, viewport })
    })
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
  }, [pdf, pageNumber, scale])
  return <canvas ref={canvasRef} className="mx-auto mb-4 rounded shadow-lg" />
}

export default function PdfViewer({ record, onClose }) {
  const [pdf, setPdf] = useState(null)
  const [error, setError] = useState(null)
  const [scale, setScale] = useState(1.1)

  useEffect(() => {
    let doc = null
    const task = pdfjsLib.getDocument({ url: `/api/records/${record.id}/content` })
    task.promise.then(
      (d) => { doc = d; setPdf(d) },
      (e) => setError(e.message || 'Could not load the PDF'),
    )
    return () => { task.destroy(); doc?.destroy() }
  }, [record.id])

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-slate-900/80 p-4"
         onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mx-auto flex h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-slate-100 shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{record.title || record.name}</div>
            <div className="truncate text-xs text-slate-500">{record.source_name} · {record.path}</div>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button className="btn-secondary !px-2" title="Zoom out"
                    onClick={() => setScale((s) => Math.max(0.5, s - 0.2))}><Minus size={14} /></button>
            <span className="w-12 text-center text-xs text-slate-500">{Math.round(scale * 100)}%</span>
            <button className="btn-secondary !px-2" title="Zoom in"
                    onClick={() => setScale((s) => Math.min(3, s + 0.2))}><Plus size={14} /></button>
            <a className="btn-secondary" href={`/api/records/${record.id}/content`}
               target="_blank" rel="noreferrer">Open in tab</a>
            <button className="rounded p-1.5 text-slate-400 hover:bg-slate-100" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {error && <p className="py-16 text-center text-sm text-rose-600">{error}</p>}
          {!pdf && !error && <p className="py-16 text-center text-sm text-slate-500">Loading document…</p>}
          {pdf && Array.from({ length: pdf.numPages }, (_, i) => (
            <Page key={i + 1} pdf={pdf} pageNumber={i + 1} scale={scale} />
          ))}
        </div>
      </div>
    </div>
  )
}
