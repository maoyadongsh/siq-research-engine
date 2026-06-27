create schema if not exists document_parser;

create table if not exists document_parser.documents (
  document_id text primary key,
  task_id text not null unique,
  collection text not null default 'default',
  document_key text not null,
  filename text,
  document_kind text,
  parser_provider text,
  file_sha256 text,
  package_path text not null,
  quality_status text,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists document_parser.parse_runs (
  parse_run_id text primary key,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  task_id text not null,
  parser_version text,
  parser_provider text,
  package_path text not null,
  status text,
  completed_at timestamptz,
  artifact_hashes jsonb not null default '{}'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists document_parser.blocks (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  block_id text not null,
  block_type text,
  sub_type text,
  page_number integer,
  reading_order integer,
  text text,
  markdown text,
  bbox jsonb not null default '[]'::jsonb,
  evidence_id text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, block_id)
);

create table if not exists document_parser.tables (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  table_id text not null,
  block_id text,
  title text,
  caption text,
  page_number integer,
  sheet_name text,
  row_count integer,
  column_count integer,
  markdown text,
  html text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, table_id)
);

create table if not exists document_parser.table_cells (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  table_id text not null,
  row_index integer not null,
  column_index integer not null,
  text text,
  bbox jsonb not null default '[]'::jsonb,
  evidence_id text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, table_id, row_index, column_index)
);

create table if not exists document_parser.logical_tables (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  logical_table_id text not null,
  title text,
  fragment_table_ids jsonb not null default '[]'::jsonb,
  merge_status text,
  merge_confidence numeric,
  markdown text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, logical_table_id)
);

create table if not exists document_parser.table_relations (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  relation_id text not null,
  source_table_id text,
  target_table_id text,
  relation_type text,
  confidence numeric,
  review_status text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, relation_id)
);

create table if not exists document_parser.figures (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  image_id text not null,
  block_id text,
  figure_type text,
  page_number integer,
  image_path text,
  caption text,
  ocr_text text,
  evidence_id text,
  bbox jsonb not null default '[]'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, image_id)
);

create table if not exists document_parser.extractions (
  extract_id text primary key,
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  status text,
  schema_json jsonb not null default '{}'::jsonb,
  result_json jsonb not null default '{}'::jsonb,
  evidence_map jsonb not null default '{}'::jsonb,
  validation_report jsonb not null default '{}'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists document_parser.sources (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  evidence_id text not null,
  document_id text not null references document_parser.documents(document_id) on delete cascade,
  source_type text,
  artifact text,
  block_id text,
  table_id text,
  logical_table_id text,
  image_id text,
  page_number integer,
  bbox jsonb not null default '[]'::jsonb,
  quote text,
  open_source_url text,
  open_artifact_url text,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, evidence_id)
);

create table if not exists document_parser.artifacts (
  parse_run_id text not null references document_parser.parse_runs(parse_run_id) on delete cascade,
  artifact_path text not null,
  artifact_type text,
  sha256 text,
  size_bytes bigint,
  raw jsonb not null default '{}'::jsonb,
  primary key (parse_run_id, artifact_path)
);

create index if not exists idx_document_parser_documents_collection on document_parser.documents(collection);
create index if not exists idx_document_parser_blocks_document_order on document_parser.blocks(document_id, reading_order);
create index if not exists idx_document_parser_sources_document on document_parser.sources(document_id);
create index if not exists idx_document_parser_sources_block on document_parser.sources(block_id);
create index if not exists idx_document_parser_sources_table on document_parser.sources(table_id);
create index if not exists idx_document_parser_sources_image on document_parser.sources(image_id);
create index if not exists idx_document_parser_artifacts_run on document_parser.artifacts(parse_run_id);
