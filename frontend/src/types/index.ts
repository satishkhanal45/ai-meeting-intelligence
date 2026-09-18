export type ActionStatus = 'open' | 'in_progress' | 'done' | 'cancelled'
export type ActionPriority = 'high' | 'medium' | 'low'

export interface ActionItem {
  id: number
  owner: string
  task: string
  priority: ActionPriority
  status: ActionStatus
}

export interface OwnedActionItem extends ActionItem {
  meeting_id: string
  meeting_title: string
}

export interface Deadline {
  id: number
  description: string
  date: string
  type: string
}

export interface DatedDeadline extends Deadline {
  meeting_id: string
  meeting_title: string
}

export interface Decision {
  id: number
  decision: string
  rationale: string
}

export interface Transcript {
  raw_text: string
  cleaned_text: string
}

export interface Summary {
  executive_summary: string
}

export interface GraphData {
  graph_json: string
}

export interface MeetingListItem {
  id: string
  title: string
  date: string
  participants: string[]
  provider: string
  created_at: string
  action_item_count: number
  decision_count: number
  chunk_total: number
  chunk_failures: number
  degraded: boolean
}

export interface Meeting {
  id: string
  title: string
  date: string
  participants: string[]
  provider: string
  model: string
  processing_time: number
  transcript: Transcript
  summary: Summary
  action_items: ActionItem[]
  deadlines: Deadline[]
  decisions: Decision[]
  graph_data: GraphData
  chunk_total: number
  chunk_failures: number
  input_tokens: number
  output_tokens: number
  served_by: string[]
  degraded: boolean
  used_fallback: boolean
}

export interface GraphEntity {
  id: string
  label: string
  type: string
  properties: Record<string, string>
}

export interface GraphRelationship {
  source: string
  target: string
  label: string
}

export interface KnowledgeGraph {
  entities: GraphEntity[]
  relationships: GraphRelationship[]
}

export interface Stats {
  total_meetings: number
  unique_participants: number
  total_action_items: number
  total_decisions: number
}

export interface Config {
  default_provider: string
  default_temperature: number
  default_chunk_size: number
  default_chunk_overlap: number
  configured_providers: string[]
}

export interface Health {
  status: string
  database: boolean
  configured_providers: string[]
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Job {
  job_id: string
  status: JobStatus
  stage: string
  message: string
  completed: number
  total: number
  fraction: number
  meeting_id: string | null
  error: string | null
  error_id: string | null
  created_at: string
  finished_at: string | null
}

export interface ProviderInfo {
  name: string
  configured: boolean
  default_model: string
  available_models: string[]
}

export interface ProvidersResponse {
  providers: ProviderInfo[]
  default_provider: string
  fallback_chain: string[]
}

export interface PersonSummary {
  id: string
  name: string
  meeting_count: number
  action_item_count: number
  open_action_item_count: number
  last_seen: string
}

export interface PersonDetail {
  id: string
  name: string
  meetings: MeetingListItem[]
  action_items: OwnedActionItem[]
  open_action_items: number
}

export type ItemKind = 'action-items' | 'deadlines' | 'decisions'
