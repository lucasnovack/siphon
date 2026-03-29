// Lazy-loaded CodeMirror SQL editor
import { useEffect, useRef } from 'react'
import { EditorView, basicSetup } from 'codemirror'
import { sql } from '@codemirror/lang-sql'
import { EditorState } from '@codemirror/state'

interface QueryEditorProps {
  value: string
  onChange: (value: string) => void
  readOnly?: boolean
}

export default function QueryEditor({ value, onChange, readOnly = false }: QueryEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const state = EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
        sql(),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChange(update.state.doc.toString())
          }
        }),
        EditorView.editable.of(!readOnly),
        EditorView.theme({
          '&': { minHeight: '160px', borderRadius: '6px', border: '1px solid hsl(var(--border))' },
          '.cm-scroller': { fontFamily: 'monospace', fontSize: '13px' },
        }),
      ],
    })

    viewRef.current = new EditorView({ state, parent: containerRef.current })

    return () => {
      viewRef.current?.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Sync external value changes (e.g. form reset)
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    if (view.state.doc.toString() !== value) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      })
    }
  }, [value])

  return <div ref={containerRef} />
}
