export interface ActionItem {
  owner: string
  task: string
  priority: string
  status: string
}

export interface Deadline {
  description: string
  date: string
  type: string
}

export interface Decision {
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
}

export interface Meeting {
  id: string
  title: string
  date: string
  participants: string[]
  provider: string
  processing_time: number
  transcript: Transcript
  summary: Summary
  action_items: ActionItem[]
  deadlines: Deadline[]
  decisions: Decision[]
  graph_data: GraphData
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
