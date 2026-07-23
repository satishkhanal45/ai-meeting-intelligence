import { useEffect, useRef } from 'react'
import ForceGraph3D from '3d-force-graph'
import * as THREE from 'three'
import SpriteText from 'three-spritetext'
import type { KnowledgeGraph } from '../types'

interface GraphViewerProps {
  graph: KnowledgeGraph | null
}

const NODE_COLORS: Record<string, string> = {
  person: '#4A90D9',
  task: '#27AE60',
  deadline: '#F1C40F',
  decision: '#8E44AD',
  milestone: '#E67E22',
  information: '#95A5A6',
  critical: '#E74C3C',
}

const TYPE_ICONS: Record<string, string> = {
  person: '👤',
  task: '📋',
  deadline: '📅',
  decision: '🎯',
  milestone: '🏁',
  information: 'ℹ️',
  critical: '⚠️',
}

const LINK_COLORS: Record<string, string> = {
  assigned_to: '#6C63FF',
  depends_on: '#E74C3C',
  related_to: '#4ECDC4',
  mentioned_in: '#FFA07A',
  involves: '#45B7D1',
  leads_to: '#F39C12',
  part_of: '#95A5A6',
}

const DEFAULT_COLOR = '#95A5A6'
const DEFAULT_LINK_COLOR = '#555869'

export default function GraphViewer({ graph }: GraphViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    if (graphRef.current) {
      graphRef.current.graphData({ nodes: [], links: [] })._destructor()
      graphRef.current = null
    }
    while (el.firstChild) el.removeChild(el.firstChild)

    if (!graph || !graph.entities || graph.entities.length === 0) return

    const freq: Record<string, number> = {}
    for (const r of graph.relationships) {
      freq[r.source] = (freq[r.source] || 0) + 1
      freq[r.target] = (freq[r.target] || 0) + 1
    }
    const maxDegree = Math.max(...Object.values(freq), 1)

    const nodeSize = (id: string) => {
      const d = freq[id] || 1
      return 0.5 + 1.5 * (d / maxDegree)
    }

    const nodes = graph.entities.map((e) => ({
      id: e.id,
      name: e.label,
      type: e.type,
      color: NODE_COLORS[e.type] || DEFAULT_COLOR,
      properties: e.properties || {},
      val: nodeSize(e.id),
    }))

    const linkColorFn = (r: any) => LINK_COLORS[r.label] || DEFAULT_LINK_COLOR

    const links = graph.relationships.map((r) => ({
      source: r.source,
      target: r.target,
      label: r.label,
      color: linkColorFn(r),
    }))

    const fg = new ForceGraph3D(el)
    graphRef.current = fg
      .graphData({ nodes, links })
      .backgroundColor('#0a0b10')
      .width(el.clientWidth)
      .height(600)
      .nodeRelSize(4)
      .nodeId('id')
      .nodeVal('val')
      .nodeColor((n: any) => n.color)
      .linkColor((l: any) => l.color)
      .linkWidth(0.6)
      .linkOpacity(0.6)
      .linkCurvature(0.15)
      .linkDirectionalArrowLength(4)
      .linkDirectionalArrowColor((l: any) => l.color)
      .linkDirectionalArrowRelPos(0.95)
      .linkDirectionalParticles(2)
      .linkDirectionalParticleSpeed(0.008)
      .linkDirectionalParticleWidth(2)
      .linkDirectionalParticleColor((l: any) => l.color)
      .warmupTicks(200)
      .cooldownTicks(50)
      .cooldownTime(3000)
      .nodeThreeObject((node: any) => {
        const icon = TYPE_ICONS[node.type] || '●'
        const color = node.color
        const textColor = node.type === 'deadline' ? '#1a1a1a' : '#ffffff'

        const sprite = new SpriteText(`${icon}  ${node.name}`)
        sprite.color = textColor
        sprite.backgroundColor = color
        sprite.padding = [8, 5]
        sprite.borderRadius = 8
        sprite.fontSize = 13
        sprite.fontWeight = '600'

        const n = Math.max(1, node.val || 1)
        const r = 0.5 + n * 2
        sprite.textHeight = r * 2

        const glow = new THREE.Mesh(
          new THREE.RingGeometry(r + 0.15, r + 0.6, 24),
          new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.25,
            side: THREE.DoubleSide,
            depthWrite: false,
          })
        )
        glow.position.z = -0.1

        const group = new THREE.Group()
        group.add(glow)
        group.add(sprite)

        const status = node.properties?.status
        if (status) {
          const statusColors: Record<string, string> = { open: '#E74C3C', in_progress: '#F39C12', done: '#27AE60' }
          const sc = statusColors[status] || '#95A5A6'
          const badge = new THREE.Mesh(
            new THREE.SphereGeometry(0.35, 10, 10),
            new THREE.MeshBasicMaterial({ color: sc })
          )
          badge.position.set(r + 0.9, 1, 0)
          group.add(badge)
        }

        const priority = node.properties?.priority
        if (priority) {
          const priorityColors: Record<string, string> = { high: '#E74C3C', medium: '#F39C12', low: '#95A5A6' }
          const pc = priorityColors[priority] || '#95A5A6'
          const badge = new THREE.Mesh(
            new THREE.SphereGeometry(0.25, 10, 10),
            new THREE.MeshBasicMaterial({ color: pc })
          )
          badge.position.set(r + 0.9, -1, 0)
          group.add(badge)
        }

        return group
      })
      .onNodeHover((node: any) => {
        el.style.cursor = node ? 'pointer' : 'default'
      })
      .onEngineStop(() => fg.zoomToFit(400, 50))

    const handleResize = () => {
      fg.width(el.clientWidth)
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      if (graphRef.current) {
        graphRef.current.graphData({ nodes: [], links: [] })._destructor()
        graphRef.current = null
      }
    }
  }, [graph])

  if (!graph || !graph.entities || graph.entities.length === 0) {
    return <div className="info">No knowledge graph data available.</div>
  }

  return (
    <div>
      <div style={{
        display: 'flex', gap: 20, marginBottom: 10, fontSize: '0.78rem',
        color: 'var(--text-muted)', alignItems: 'center', flexWrap: 'wrap'
      }}>
        <span>Nodes: {graph.entities.length}</span>
        <span>Edges: {graph.relationships.length}</span>
        <span style={{ fontSize: '0.72rem', opacity: 0.6 }}>
          Drag nodes · Scroll to zoom · Click node to inspect
        </span>
      </div>
      <div ref={containerRef} style={{ width: '100%', height: 600, borderRadius: 10, overflow: 'hidden' }} />
    </div>
  )
}
