import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { ApiError, api } from '../api'
import type {
  DatasetTable,
  RowPage,
  SessionDetail,
  SessionGraph,
  TableGraph,
} from '../types'

const PREVIEW_ROWS = 5
const DRILL_ROWS = 60
const ZOOM_STEP = 1.35

//: Fitting a sparse graph into the canvas can leave the labels too small to
//: read, so the opening view never zooms out past this — panning is a better
//: trade than an unreadable picture.
const MIN_READABLE_ZOOM = 0.75

interface Drill {
  tableId: string
  tableName: string
}

interface Props {
  session: SessionDetail | null
  onViewRows: (tableId: string) => void
}

function message(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : String(cause)
}

/** Cytoscape needs resolved colours, so the theme's tokens are read once from
 *  the document rather than being repeated as literals here. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function stylesheet(): cytoscape.StylesheetJson {
  const panel = token('--panel', '#ffffff')
  const line = token('--line-strong', '#cdd3dd')
  const text = token('--text', '#1a1e27')
  const muted = token('--muted', '#6b7382')
  const accent = token('--accent', '#2563eb')
  const accentSoft = token('--accent-soft', '#eef3ff')
  const bg = token('--bg', '#f5f6f8')
  const font = 'system-ui, -apple-system, Segoe UI, sans-serif'

  return [
    // Every node is the same plain circle with its label underneath. Shape and
    // colour are left to say one thing only — whether a node is selected,
    // related to the selection, or neither — so nothing else competes with it.
    {
      selector: 'node',
      style: {
        shape: 'ellipse',
        width: 44,
        height: 44,
        'background-color': panel,
        'border-width': 2,
        'border-color': line,
        label: 'data(label)',
        'text-wrap': 'wrap',
        'text-max-width': '130px',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 7,
        'line-height': 1.3,
        'font-size': 11,
        'font-family': font,
        color: text,
        // Labels sit under the circles and can fall across an edge, so they
        // carry the canvas colour behind them.
        'text-background-color': bg,
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
      },
    },
    {
      selector: 'edge',
      style: {
        width: 1.5,
        'line-color': line,
        'target-arrow-color': line,
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.9,
        'curve-style': 'bezier',
        label: 'data(label)',
        'font-size': 10,
        'font-family': font,
        color: muted,
        'text-background-color': bg,
        'text-background-opacity': 1,
        'text-background-padding': '3px',
        // Hidden until something is selected: on a dense graph the column
        // pairs collide with the node labels and with each other, and the
        // ones worth reading are the ones attached to the node in hand.
        'text-opacity': 0,
      },
    },
    // Everything outside the selection is pushed back rather than hidden, so
    // the shape of the graph stays readable while one part of it is in focus.
    { selector: '.faded', style: { opacity: 0.12, 'text-opacity': 0.08 } },
    {
      selector: 'node.related',
      style: { 'background-color': accentSoft, 'border-color': accent, color: accent },
    },
    {
      selector: 'edge.related',
      style: {
        'line-color': accent,
        'target-arrow-color': accent,
        color: accent,
        width: 2,
        'text-opacity': 1,
      },
    },
    // The selection is the one filled circle, so it reads at a glance against
    // the related nodes, which are only outlined.
    {
      selector: 'node.focus',
      style: { 'background-color': accent, 'border-color': accent, color: accent },
    },
  ]
}

function schemaElements(graph: SessionGraph): cytoscape.ElementDefinition[] {
  return [
    ...graph.nodes.map((node) => ({
      data: {
        id: node.table_id,
        label: `${node.name}\n${node.row_count.toLocaleString()} rows · ${node.column_count} cols`,
      },
    })),
    ...graph.edges.map((edge) => ({
      data: {
        id: edge.relationship_id,
        source: edge.from_table_id,
        target: edge.to_table_id,
        label: edge.from_column ? `${edge.from_column} → ${edge.to_column ?? ''}` : edge.type,
      },
    })),
  ]
}

function rowElements(graph: TableGraph): cytoscape.ElementDefinition[] {
  return [
    ...graph.nodes.map((node) => ({
      data: { id: node.id, label: `${node.label}\n${node.table}` },
    })),
    ...graph.edges.map((edge) => ({
      data: { id: edge.id, source: edge.source, target: edge.target, label: edge.label },
    })),
  ]
}

export function GraphView({ session, onViewRows }: Props) {
  const [graph, setGraph] = useState<SessionGraph | null>(null)
  const [tables, setTables] = useState<DatasetTable[]>([])
  const [drill, setDrill] = useState<Drill | null>(null)
  const [rowGraph, setRowGraph] = useState<TableGraph | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [preview, setPreview] = useState<RowPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const container = useRef<HTMLDivElement>(null)
  const graphRef = useRef<cytoscape.Core | null>(null)

  const sessionId = session?.session_id ?? null
  const status = session?.status

  useEffect(() => {
    setDrill(null)
    setRowGraph(null)
    setSelectedId(null)
    setPreview(null)
    setError(null)
    if (!sessionId) {
      setGraph(null)
      setTables([])
      return
    }

    // The graph gives the shape; the table list gives each node its columns,
    // which the detail panel needs and the graph endpoint has no reason to
    // repeat for every node.
    let active = true
    Promise.all([api.getGraph(sessionId), api.getTables(sessionId)])
      .then(([shape, listed]) => {
        if (!active) return
        setGraph(shape)
        setTables(listed.tables)
      })
      .catch((cause) => active && setError(message(cause)))

    return () => {
      active = false
    }
  }, [sessionId, status])

  useEffect(() => {
    if (!drill) {
      setRowGraph(null)
      return
    }

    let active = true
    setSelectedId(null)
    api
      .getTableGraph(drill.tableId, DRILL_ROWS)
      .then((result) => active && setRowGraph(result))
      .catch((cause) => active && setError(message(cause)))

    return () => {
      active = false
    }
  }, [drill])

  const elements = useMemo(() => {
    if (drill) return rowGraph ? rowElements(rowGraph) : null
    return graph ? schemaElements(graph) : null
  }, [drill, rowGraph, graph])

  useEffect(() => {
    if (!container.current || !elements || elements.length === 0) return

    const instance = cytoscape({
      container: container.current,
      elements,
      style: stylesheet(),
      minZoom: 0.15,
      maxZoom: 3,
      wheelSensitivity: 0.2,
      layout: {
        name: 'cose',
        animate: true,
        animationDuration: 500,
        padding: 45,
        // Above the defaults, because each circle carries a label underneath
        // it that the layout does not measure and that would otherwise run
        // into the node below.
        nodeRepulsion: () => 50000,
        idealEdgeLength: () => 190,
        nodeOverlap: 36,
        componentSpacing: 120,
        gravity: 0.4,
        randomize: true,
      } as cytoscape.LayoutOptions,
    })

    instance.one('layoutstop', () => {
      if (instance.zoom() < MIN_READABLE_ZOOM) {
        instance.zoom(MIN_READABLE_ZOOM)
        instance.center()
      }
    })

    instance.on('tap', 'node', (event) => setSelectedId(event.target.id()))
    // Tapping the background clears the selection, which is how every other
    // canvas behaves and avoids a selection that cannot be undone.
    instance.on('tap', (event) => {
      if (event.target === instance) setSelectedId(null)
    })

    graphRef.current = instance
    return () => {
      instance.destroy()
      graphRef.current = null
    }
  }, [elements])

  useEffect(() => {
    const instance = graphRef.current
    if (!instance) return

    instance.batch(() => {
      instance.elements().removeClass('faded related focus')
      if (!selectedId) return

      const node = instance.getElementById(selectedId)
      // The closed neighbourhood is the node itself plus everything one hop
      // away, which is exactly what "this and what it is related to" means.
      const near = node.closedNeighborhood()
      instance.elements().difference(near).addClass('faded')
      near.addClass('related')
      node.addClass('focus')
    })
  }, [selectedId])

  useEffect(() => {
    if (drill || !selectedId) {
      setPreview(null)
      return
    }

    let active = true
    api
      .getRows(selectedId, 0, PREVIEW_ROWS)
      .then((page) => active && setPreview(page))
      .catch((cause) => active && setError(message(cause)))

    return () => {
      active = false
    }
  }, [drill, selectedId])

  const selectedNode = useMemo(
    () => graph?.nodes.find((node) => node.table_id === selectedId) ?? null,
    [graph, selectedId],
  )

  const selectedTable = useMemo(
    () => tables.find((table) => table.table_id === selectedId) ?? null,
    [tables, selectedId],
  )

  const selectedRow = useMemo(
    () => rowGraph?.nodes.find((node) => node.id === selectedId) ?? null,
    [rowGraph, selectedId],
  )

  const links = useMemo(() => {
    if (!selectedId) return []

    if (drill && rowGraph) {
      const labelOf = (id: string) => {
        const node = rowGraph.nodes.find((candidate) => candidate.id === id)
        return node ? `${node.table} · ${node.label}` : id
      }
      return rowGraph.edges
        .filter((edge) => edge.source === selectedId || edge.target === selectedId)
        .map((edge) =>
          edge.source === selectedId
            ? { direction: '→', name: labelOf(edge.target), via: edge.label }
            : { direction: '←', name: labelOf(edge.source), via: edge.label },
        )
    }

    if (!graph) return []
    return graph.edges
      .filter((edge) => edge.from_table_id === selectedId || edge.to_table_id === selectedId)
      .map((edge) => {
        const via = `${edge.from_column} → ${edge.to_column}`
        return edge.from_table_id === selectedId
          ? { direction: '→', name: edge.to_table, via }
          : { direction: '←', name: edge.from_table, via }
      })
  }, [drill, rowGraph, graph, selectedId])

  function zoomBy(factor: number) {
    const instance = graphRef.current
    if (!instance) return
    instance.zoom({
      level: instance.zoom() * factor,
      renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 },
    })
  }

  if (!session) return <p className="empty">Pick an upload to see its graph.</p>

  if (session.status === 'queued' || session.status === 'processing') {
    return (
      <p className="waiting">
        <span className="spinner" />
        <span>Still being parsed…</span>
      </p>
    )
  }

  // A failed row preview must not take the graph down with it, so the message
  // is shown beside the canvas rather than instead of it.
  if (error && !graph) return <p className="error">{error}</p>

  if (graph && graph.nodes.length === 0) {
    return <p className="empty">This upload produced no tables to graph.</p>
  }

  return (
    <div className="graph">
      <div className="graph-toolbar">
        {drill && (
          <button type="button" onClick={() => setDrill(null)}>
            ← Tables
          </button>
        )}
        <button type="button" onClick={() => zoomBy(ZOOM_STEP)} aria-label="Zoom in">
          +
        </button>
        <button type="button" onClick={() => zoomBy(1 / ZOOM_STEP)} aria-label="Zoom out">
          −
        </button>
        <button type="button" onClick={() => graphRef.current?.fit(undefined, 40)}>
          Fit
        </button>
        <span className="hint">
          {drill
            ? `${drill.tableName} · ${rowGraph?.nodes.length ?? 0} rows · ${rowGraph?.edges.length ?? 0} links`
            : `${graph?.nodes.length ?? 0} tables · ${graph?.edges.length ?? 0} relationships`}
        </span>
      </div>

      <div className="graph-canvas" ref={container} />

      {error && <p className="error">{error}</p>}

      {drill && rowGraph?.truncated && (
        <p className="hint">
          Only part of {drill.tableName} is drawn — {rowGraph.nodes.length} rows and{' '}
          {rowGraph.edges.length} links. The rest is left out.
        </p>
      )}

      {!drill && graph && graph.edges.length === 0 && (
        <p className="hint">
          No relationships were declared by this upload. Only a SQL dump carries its own foreign
          keys — links for the other formats are inferred from the data, which is not built yet.
        </p>
      )}

      {selectedNode && !drill && (
        <div className="graph-detail fade-in">
          <div className="graph-detail-head">
            <strong>{selectedNode.name}</strong>
            <span className="hint">
              {selectedNode.row_count.toLocaleString()} rows · {selectedNode.column_count} columns
            </span>
            <button
              type="button"
              onClick={() =>
                setDrill({ tableId: selectedNode.table_id, tableName: selectedNode.name })
              }
            >
              Explore rows
            </button>
            <button type="button" onClick={() => onViewRows(selectedNode.table_id)}>
              View all rows
            </button>
          </div>

          {selectedTable && (
            <div className="columns">
              {selectedTable.columns.map((column) => (
                <span key={column.name} className="column">
                  {column.name}
                  <span className="type">{column.inferred_type}</span>
                </span>
              ))}
            </div>
          )}

          <LinkList links={links} />

          {selectedTable && preview && preview.rows.length > 0 && (
            <div className="grid">
              <table>
                <thead>
                  <tr>
                    {selectedTable.columns.map((column) => (
                      <th key={column.name}>{column.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((row, index) => (
                    <tr key={index}>
                      {selectedTable.columns.map((column) => (
                        <td key={column.name}>{cell(row[column.name])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {selectedRow && drill && (
        <div className="graph-detail fade-in">
          <div className="graph-detail-head">
            <strong>{selectedRow.label}</strong>
            <span className="hint">in {selectedRow.table}</span>
          </div>

          <dl className="fields">
            {Object.entries(selectedRow.row).map(([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{cell(value)}</dd>
              </div>
            ))}
          </dl>

          <LinkList links={links} />
        </div>
      )}

      {!selectedId && (
        <p className="hint">
          {drill
            ? 'Click a row to see its fields and what it connects to.'
            : 'Click a table to see what it holds and what it links to.'}
        </p>
      )}
    </div>
  )
}

interface Link {
  direction: string
  name: string
  via: string
}

function LinkList({ links }: { links: Link[] }) {
  if (links.length === 0) return null

  return (
    <ul className="neighbours">
      {links.map((link, index) => (
        <li key={index}>
          <span className="direction">{link.direction}</span>
          <strong>{link.name}</strong>
          <span className="hint">{link.via}</span>
        </li>
      ))}
    </ul>
  )
}

function cell(value: unknown) {
  if (value === null || value === undefined) return <span className="null">null</span>
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
