import { Suspense, lazy, useState } from 'react'
import { TableExplorer } from './TableExplorer'
import type { SessionDetail } from '../types'

// The graph library is most of the bundle and only one of the two views needs
// it, so it is fetched when that view is first opened rather than on load.
const GraphView = lazy(() =>
  import('./GraphView').then((module) => ({ default: module.GraphView })),
)

type Mode = 'table' | 'graph'

interface Props {
  session: SessionDetail | null
}

export function DatasetPanel({ session }: Props) {
  const [mode, setMode] = useState<Mode>('table')
  // Set when a graph node asks to be opened as rows, so the table view knows
  // which table the user came from.
  const [focusTableId, setFocusTableId] = useState<string | null>(null)

  return (
    <section className="panel grow">
      <div className="panel-head">
        <h2>Parsed data</h2>
        {session && (
          <div className="switch" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'table'}
              className={mode === 'table' ? 'on' : undefined}
              onClick={() => setMode('table')}
            >
              Table
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'graph'}
              className={mode === 'graph' ? 'on' : undefined}
              onClick={() => setMode('graph')}
            >
              Graph
            </button>
          </div>
        )}
      </div>

      {mode === 'table' ? (
        <TableExplorer session={session} focusTableId={focusTableId} />
      ) : (
        <Suspense
          fallback={
            <p className="waiting">
              <span className="spinner" />
              <span>Loading the graph…</span>
            </p>
          }
        >
          <GraphView
            session={session}
            onViewRows={(tableId) => {
              setFocusTableId(tableId)
              setMode('table')
            }}
          />
        </Suspense>
      )}
    </section>
  )
}
