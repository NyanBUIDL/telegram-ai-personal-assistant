
CREATE TABLE ai_conversations (
	id VARCHAR(36) NOT NULL, 
	owner_id BIGINT NOT NULL, 
	summary TEXT, 
	expires_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_ai_conversations PRIMARY KEY (id)
)

;


CREATE TABLE ai_memories (
	id VARCHAR(36) NOT NULL, 
	memory_type VARCHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	scope_type VARCHAR(16) NOT NULL, 
	scope_id VARCHAR(64), 
	source_chat_id BIGINT, 
	source_message_id BIGINT, 
	confidence FLOAT NOT NULL, 
	valid_from DATETIME, 
	valid_until DATETIME, 
	superseded_by VARCHAR(36), 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_ai_memories PRIMARY KEY (id), 
	CONSTRAINT fk_ai_memories_superseded_by_ai_memories FOREIGN KEY(superseded_by) REFERENCES ai_memories (id)
)

;
CREATE INDEX ix_ai_memories_memory_type ON ai_memories (memory_type);
CREATE INDEX ix_ai_memories_scope_id ON ai_memories (scope_id);
CREATE INDEX ix_ai_memories_status ON ai_memories (status);

CREATE TABLE ai_memory_candidates (
	id VARCHAR(36) NOT NULL, 
	content TEXT NOT NULL, 
	proposed_scope VARCHAR(64) NOT NULL, 
	source_chat_id BIGINT, 
	source_message_id BIGINT, 
	confidence FLOAT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_ai_memory_candidates PRIMARY KEY (id)
)

;


CREATE TABLE ai_query_cache (
	cache_key VARCHAR(64) NOT NULL, 
	normalized_query_hash VARCHAR(64) NOT NULL, 
	scope_chat_id BIGINT, 
	knowledge_version VARCHAR(128) NOT NULL, 
	route VARCHAR(64) NOT NULL, 
	feature VARCHAR(64) NOT NULL, 
	provider VARCHAR(32), 
	model VARCHAR(160), 
	response TEXT NOT NULL, 
	citations JSON, 
	expires_at DATETIME NOT NULL, 
	hit_count INTEGER NOT NULL, 
	last_hit_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_ai_query_cache PRIMARY KEY (cache_key)
)

;
CREATE INDEX ix_ai_query_cache_expires_at ON ai_query_cache (expires_at);
CREATE INDEX ix_ai_query_cache_feature ON ai_query_cache (feature);
CREATE INDEX ix_ai_query_cache_normalized_query_hash ON ai_query_cache (normalized_query_hash);
CREATE INDEX ix_ai_query_cache_route ON ai_query_cache (route);
CREATE INDEX ix_ai_query_cache_scope_chat_id ON ai_query_cache (scope_chat_id);

CREATE TABLE ai_usage (
	id INTEGER NOT NULL, 
	occurred_at DATETIME NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	operation VARCHAR(64) NOT NULL, 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	estimated_cost_usd FLOAT NOT NULL, 
	success BOOLEAN NOT NULL, 
	provider VARCHAR(32), 
	feature VARCHAR(64), 
	route VARCHAR(64), 
	chat_id BIGINT, 
	cached_tokens INTEGER NOT NULL, 
	embedding_tokens INTEGER NOT NULL, 
	latency_ms FLOAT, 
	cache_hit BOOLEAN NOT NULL, 
	is_local BOOLEAN NOT NULL, 
	fallback_used BOOLEAN NOT NULL, 
	error_code VARCHAR(64), 
	CONSTRAINT pk_ai_usage PRIMARY KEY (id)
)

;
CREATE INDEX ix_ai_usage_chat_id ON ai_usage (chat_id);
CREATE INDEX ix_ai_usage_feature ON ai_usage (feature);
CREATE INDEX ix_ai_usage_occurred_at ON ai_usage (occurred_at);
CREATE INDEX ix_ai_usage_provider ON ai_usage (provider);
CREATE INDEX ix_ai_usage_route ON ai_usage (route);

CREATE TABLE app_settings (
	"key" VARCHAR(128) NOT NULL, 
	value JSON, 
	description VARCHAR(512), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_app_settings PRIMARY KEY ("key")
)

;


CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	occurred_at DATETIME NOT NULL, 
	actor_id BIGINT, 
	action VARCHAR(128) NOT NULL, 
	target_type VARCHAR(64), 
	target_id VARCHAR(128), 
	outcome VARCHAR(32) NOT NULL, 
	reason TEXT, 
	details_redacted JSON, 
	correlation_id VARCHAR(36), 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id)
)

;
CREATE INDEX ix_audit_logs_action ON audit_logs (action);
CREATE INDEX ix_audit_logs_correlation_id ON audit_logs (correlation_id);
CREATE INDEX ix_audit_logs_occurred_at ON audit_logs (occurred_at);

CREATE TABLE background_jobs (
	id VARCHAR(36) NOT NULL, 
	job_type VARCHAR(64) NOT NULL, 
	payload JSON, 
	status VARCHAR(32) NOT NULL, 
	attempts INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	run_after DATETIME, 
	locked_by VARCHAR(128), 
	locked_at DATETIME, 
	paused_at DATETIME, 
	last_error TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_background_jobs PRIMARY KEY (id)
)

;
CREATE INDEX ix_background_jobs_job_type ON background_jobs (job_type);
CREATE INDEX ix_background_jobs_run_after ON background_jobs (run_after);
CREATE INDEX ix_background_jobs_status ON background_jobs (status);

CREATE TABLE bot_queries (
	id VARCHAR(36) NOT NULL, 
	owner_id BIGINT NOT NULL, 
	query_redacted TEXT NOT NULL, 
	response_redacted TEXT, 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_bot_queries PRIMARY KEY (id)
)

;


CREATE TABLE health_checks (
	id INTEGER NOT NULL, 
	checked_at DATETIME NOT NULL, 
	component VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	latency_ms FLOAT, 
	details JSON, 
	CONSTRAINT pk_health_checks PRIMARY KEY (id)
)

;
CREATE INDEX ix_health_checks_checked_at ON health_checks (checked_at);

CREATE TABLE knowledge_cards (
	id VARCHAR(36) NOT NULL, 
	topic VARCHAR(512) NOT NULL, 
	summary TEXT NOT NULL, 
	facts JSON, 
	version INTEGER NOT NULL, 
	verification_status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_knowledge_cards PRIMARY KEY (id)
)

;
CREATE INDEX ix_knowledge_cards_topic ON knowledge_cards (topic);

CREATE TABLE moderation_rules (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	rule_json JSON NOT NULL, 
	mode VARCHAR(32) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_moderation_rules PRIMARY KEY (id)
)

;
CREATE INDEX ix_moderation_rules_chat_id ON moderation_rules (chat_id);

CREATE TABLE pending_actions (
	action_id VARCHAR(36) NOT NULL, 
	action_type VARCHAR(64) NOT NULL, 
	chat_id BIGINT, 
	message_id BIGINT, 
	requested_by BIGINT NOT NULL, 
	payload JSON NOT NULL, 
	preview TEXT, 
	reason TEXT, 
	status VARCHAR(32) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	confirmed_at DATETIME, 
	executed_at DATETIME, 
	error TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_pending_actions PRIMARY KEY (action_id)
)

;
CREATE INDEX ix_pending_actions_action_type ON pending_actions (action_type);
CREATE INDEX ix_pending_actions_chat_id ON pending_actions (chat_id);
CREATE INDEX ix_pending_actions_expires_at ON pending_actions (expires_at);
CREATE INDEX ix_pending_actions_status ON pending_actions (status);

CREATE TABLE projects (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	description TEXT, 
	status VARCHAR(32) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_projects PRIMARY KEY (id)
)

;
CREATE INDEX ix_projects_name ON projects (name);

CREATE TABLE runtime_metrics (
	id INTEGER NOT NULL, 
	collected_at DATETIME NOT NULL, 
	process_id INTEGER NOT NULL, 
	process_name VARCHAR(128) NOT NULL, 
	rss_bytes BIGINT, 
	cpu_percent FLOAT, 
	vram_bytes BIGINT, 
	data_bytes BIGINT, 
	vector_bytes BIGINT, 
	media_bytes BIGINT, 
	queued_jobs INTEGER NOT NULL, 
	running_jobs INTEGER NOT NULL, 
	paused_jobs INTEGER NOT NULL, 
	details JSON, 
	CONSTRAINT pk_runtime_metrics PRIMARY KEY (id)
)

;
CREATE INDEX ix_runtime_metrics_collected_at ON runtime_metrics (collected_at);

CREATE TABLE summaries (
	id VARCHAR(36) NOT NULL, 
	scope_type VARCHAR(32) NOT NULL, 
	scope_id VARCHAR(64) NOT NULL, 
	content TEXT NOT NULL, 
	sources JSON, 
	period_start DATETIME, 
	period_end DATETIME, 
	content_hash VARCHAR(64), 
	model VARCHAR(160), 
	provider VARCHAR(32), 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	stale BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_summaries PRIMARY KEY (id)
)

;
CREATE INDEX ix_summaries_content_hash ON summaries (content_hash);
CREATE INDEX ix_summaries_stale ON summaries (stale);

CREATE TABLE sync_states (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	last_message_id BIGINT, 
	last_message_date DATETIME, 
	state VARCHAR(32) NOT NULL, 
	error TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_sync_states PRIMARY KEY (id), 
	CONSTRAINT uq_sync_states_chat_id UNIQUE (chat_id)
)

;


CREATE TABLE tags (
	id INTEGER NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_tags PRIMARY KEY (id), 
	CONSTRAINT uq_tags_name UNIQUE (name)
)

;


CREATE TABLE telegram_accounts (
	id INTEGER NOT NULL, 
	telegram_user_id BIGINT, 
	username VARCHAR(255), 
	phone_masked VARCHAR(32), 
	is_owner_paired BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	last_authenticated_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_accounts PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_accounts_telegram_user_id UNIQUE (telegram_user_id)
)

;


CREATE TABLE telegram_chat_permissions (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	permission VARCHAR(64) NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_chat_permissions PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_chat_permissions_chat_id UNIQUE (chat_id, permission)
)

;
CREATE INDEX ix_telegram_chat_permissions_chat_id ON telegram_chat_permissions (chat_id);

CREATE TABLE telegram_chat_policies (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	allowed BOOLEAN NOT NULL, 
	template VARCHAR(64), 
	revoked_at DATETIME, 
	revocation_memory_action VARCHAR(16), 
	ai_mode VARCHAR(32) NOT NULL, 
	preferred_cloud_provider VARCHAR(32), 
	cloud_fallback BOOLEAN NOT NULL, 
	retention_days INTEGER, 
	max_messages INTEGER, 
	max_storage_mb INTEGER, 
	max_vectors INTEGER, 
	ai_efficiency_preset VARCHAR(32), 
	filtering_level VARCHAR(32) NOT NULL, 
	rag_top_k INTEGER, 
	rag_max_context_tokens INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_chat_policies PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_chat_policies_chat_id UNIQUE (chat_id)
)

;
CREATE INDEX ix_telegram_chat_policies_ai_mode ON telegram_chat_policies (ai_mode);
CREATE INDEX ix_telegram_chat_policies_allowed ON telegram_chat_policies (allowed);

CREATE TABLE telegram_chats (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	title VARCHAR(512), 
	username VARCHAR(255), 
	chat_type VARCHAR(32) NOT NULL, 
	account_rights JSON, 
	last_seen_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_chats PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_chats_chat_id UNIQUE (chat_id)
)

;
CREATE INDEX ix_telegram_chats_title ON telegram_chats (title);

CREATE TABLE telegram_users (
	id INTEGER NOT NULL, 
	telegram_user_id BIGINT NOT NULL, 
	username VARCHAR(255), 
	display_name VARCHAR(512), 
	is_bot BOOLEAN NOT NULL, 
	metadata_json JSON, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_users PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_users_telegram_user_id UNIQUE (telegram_user_id)
)

;
CREATE INDEX ix_telegram_users_username ON telegram_users (username);

CREATE TABLE vector_stores (
	store_id VARCHAR(128) NOT NULL, 
	path VARCHAR(512) NOT NULL, 
	collection VARCHAR(128) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	model VARCHAR(160), 
	embedding_version VARCHAR(64), 
	dimension INTEGER, 
	role VARCHAR(32) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	last_reconciled_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_vector_stores PRIMARY KEY (store_id), 
	CONSTRAINT uq_vector_stores_path UNIQUE (path)
)

;
CREATE INDEX ix_vector_stores_role ON vector_stores (role);
CREATE INDEX ix_vector_stores_state ON vector_stores (state);

CREATE TABLE ai_conversation_messages (
	id INTEGER NOT NULL, 
	conversation_id VARCHAR(36) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	token_count INTEGER, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_ai_conversation_messages PRIMARY KEY (id), 
	CONSTRAINT fk_ai_conversation_messages_conversation_id_ai_conversations FOREIGN KEY(conversation_id) REFERENCES ai_conversations (id) ON DELETE CASCADE
)

;


CREATE TABLE ai_memory_conflicts (
	id VARCHAR(36) NOT NULL, 
	memory_a_id VARCHAR(36) NOT NULL, 
	memory_b_id VARCHAR(36) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	resolution TEXT, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_ai_memory_conflicts PRIMARY KEY (id), 
	CONSTRAINT fk_ai_memory_conflicts_memory_a_id_ai_memories FOREIGN KEY(memory_a_id) REFERENCES ai_memories (id), 
	CONSTRAINT fk_ai_memory_conflicts_memory_b_id_ai_memories FOREIGN KEY(memory_b_id) REFERENCES ai_memories (id)
)

;


CREATE TABLE ai_memory_sources (
	id INTEGER NOT NULL, 
	memory_id VARCHAR(36) NOT NULL, 
	chat_id BIGINT, 
	message_id BIGINT, 
	citation TEXT, 
	CONSTRAINT pk_ai_memory_sources PRIMARY KEY (id), 
	CONSTRAINT fk_ai_memory_sources_memory_id_ai_memories FOREIGN KEY(memory_id) REFERENCES ai_memories (id) ON DELETE CASCADE
)

;


CREATE TABLE knowledge_card_sources (
	id INTEGER NOT NULL, 
	knowledge_card_id VARCHAR(36) NOT NULL, 
	chat_id BIGINT NOT NULL, 
	message_id BIGINT NOT NULL, 
	CONSTRAINT pk_knowledge_card_sources PRIMARY KEY (id), 
	CONSTRAINT fk_knowledge_card_sources_knowledge_card_id_knowledge_cards FOREIGN KEY(knowledge_card_id) REFERENCES knowledge_cards (id) ON DELETE CASCADE
)

;


CREATE TABLE knowledge_sources (
	chat_id BIGINT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	requested_for_learning BOOLEAN NOT NULL, 
	mysql_message_count INTEGER NOT NULL, 
	text_message_count INTEGER NOT NULL, 
	last_indexed_count INTEGER NOT NULL, 
	vector_count INTEGER NOT NULL, 
	message_storage_bytes BIGINT NOT NULL, 
	media_storage_bytes BIGINT NOT NULL, 
	last_job_id VARCHAR(36), 
	last_error TEXT, 
	owner_note TEXT, 
	last_learned_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_knowledge_sources PRIMARY KEY (chat_id), 
	CONSTRAINT fk_knowledge_sources_chat_id_telegram_chats FOREIGN KEY(chat_id) REFERENCES telegram_chats (chat_id) ON DELETE CASCADE
)

;
CREATE INDEX ix_knowledge_sources_last_job_id ON knowledge_sources (last_job_id);
CREATE INDEX ix_knowledge_sources_last_learned_at ON knowledge_sources (last_learned_at);
CREATE INDEX ix_knowledge_sources_requested_for_learning ON knowledge_sources (requested_for_learning);
CREATE INDEX ix_knowledge_sources_status ON knowledge_sources (status);

CREATE TABLE tasks (
	id VARCHAR(36) NOT NULL, 
	title VARCHAR(512) NOT NULL, 
	description TEXT, 
	status VARCHAR(32) NOT NULL, 
	priority VARCHAR(16) NOT NULL, 
	project_id VARCHAR(36), 
	due_at DATETIME, 
	completed_at DATETIME, 
	assignee VARCHAR(255), 
	source VARCHAR(32) NOT NULL, 
	ai_confirmed BOOLEAN NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_tasks PRIMARY KEY (id), 
	CONSTRAINT fk_tasks_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL
)

;
CREATE INDEX ix_tasks_due_at ON tasks (due_at);
CREATE INDEX ix_tasks_status ON tasks (status);

CREATE TABLE telegram_messages (
	id INTEGER NOT NULL, 
	chat_id BIGINT NOT NULL, 
	message_id BIGINT NOT NULL, 
	sender_id BIGINT, 
	text TEXT, 
	sent_at DATETIME NOT NULL, 
	edited_at DATETIME, 
	reply_to_message_id BIGINT, 
	is_outgoing BOOLEAN NOT NULL, 
	is_deleted BOOLEAN NOT NULL, 
	has_media BOOLEAN NOT NULL, 
	metadata_json JSON, 
	normalized_text TEXT, 
	content_hash VARCHAR(64), 
	embedding_version VARCHAR(64), 
	embedding_provider VARCHAR(32), 
	embedding_model VARCHAR(160), 
	vector_status VARCHAR(32) NOT NULL, 
	embedded_at DATETIME, 
	embedding_error TEXT, 
	embedding_skip_reason VARCHAR(32), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_messages PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_messages_chat_message UNIQUE (chat_id, message_id), 
	CONSTRAINT fk_telegram_messages_chat_id_telegram_chats FOREIGN KEY(chat_id) REFERENCES telegram_chats (chat_id) ON DELETE CASCADE
)

;
CREATE INDEX ft_messages_text ON telegram_messages (text);
CREATE INDEX ix_messages_chat_date ON telegram_messages (chat_id, sent_at);
CREATE INDEX ix_messages_sender_date ON telegram_messages (sender_id, sent_at);
CREATE INDEX ix_telegram_messages_content_hash ON telegram_messages (content_hash);
CREATE INDEX ix_telegram_messages_embedding_skip_reason ON telegram_messages (embedding_skip_reason);
CREATE INDEX ix_telegram_messages_sender_id ON telegram_messages (sender_id);
CREATE INDEX ix_telegram_messages_vector_status ON telegram_messages (vector_status);

CREATE TABLE vector_source_coverage (
	store_id VARCHAR(128) NOT NULL, 
	chat_id BIGINT NOT NULL, 
	mysql_total INTEGER NOT NULL, 
	eligible_total INTEGER NOT NULL, 
	active_vector_count INTEGER NOT NULL, 
	missing_count INTEGER NOT NULL, 
	orphan_count INTEGER NOT NULL, 
	coverage_state VARCHAR(32) NOT NULL, 
	reconciled_at DATETIME NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_vector_source_coverage PRIMARY KEY (store_id, chat_id), 
	CONSTRAINT fk_vector_source_coverage_store_id_vector_stores FOREIGN KEY(store_id) REFERENCES vector_stores (store_id) ON DELETE CASCADE
)

;


CREATE TABLE reminders (
	id VARCHAR(36) NOT NULL, 
	task_id VARCHAR(36), 
	remind_at DATETIME NOT NULL, 
	message TEXT NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	sent_at DATETIME, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_reminders PRIMARY KEY (id), 
	CONSTRAINT fk_reminders_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_reminders_remind_at ON reminders (remind_at);

CREATE TABLE task_history (
	id INTEGER NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	changed_at DATETIME NOT NULL, 
	changed_by VARCHAR(64) NOT NULL, 
	old_values JSON, 
	new_values JSON, 
	CONSTRAINT pk_task_history PRIMARY KEY (id), 
	CONSTRAINT fk_task_history_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
)

;


CREATE TABLE task_sources (
	id INTEGER NOT NULL, 
	task_id VARCHAR(36) NOT NULL, 
	chat_id BIGINT, 
	message_id BIGINT, 
	source_text TEXT, 
	CONSTRAINT pk_task_sources PRIMARY KEY (id), 
	CONSTRAINT fk_task_sources_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
)

;


CREATE TABLE task_tags (
	task_id VARCHAR(36) NOT NULL, 
	tag_id INTEGER NOT NULL, 
	CONSTRAINT pk_task_tags PRIMARY KEY (task_id, tag_id), 
	CONSTRAINT fk_task_tags_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
	CONSTRAINT fk_task_tags_tag_id_tags FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
)

;


CREATE TABLE telegram_attachments (
	id INTEGER NOT NULL, 
	telegram_message_id BIGINT NOT NULL, 
	telegram_file_id VARCHAR(512), 
	file_name VARCHAR(512), 
	mime_type VARCHAR(255), 
	size_bytes BIGINT, 
	sha256 VARCHAR(64), 
	local_path VARCHAR(1024), 
	metadata_json JSON, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_attachments PRIMARY KEY (id), 
	CONSTRAINT fk_telegram_attachments_telegram_message_id_telegram_messages FOREIGN KEY(telegram_message_id) REFERENCES telegram_messages (id) ON DELETE CASCADE
)

;
CREATE INDEX ix_telegram_attachments_sha256 ON telegram_attachments (sha256);

CREATE TABLE telegram_message_versions (
	id INTEGER NOT NULL, 
	telegram_message_id BIGINT NOT NULL, 
	text TEXT, 
	version_number INTEGER NOT NULL, 
	captured_at DATETIME NOT NULL, 
	CONSTRAINT pk_telegram_message_versions PRIMARY KEY (id), 
	CONSTRAINT uq_telegram_message_versions_telegram_message_id UNIQUE (telegram_message_id, version_number), 
	CONSTRAINT fk_telegram_message_versions_telegram_message_id_telegram_messages FOREIGN KEY(telegram_message_id) REFERENCES telegram_messages (id) ON DELETE CASCADE
)

;


CREATE TABLE telegram_reactions (
	id INTEGER NOT NULL, 
	telegram_message_id BIGINT NOT NULL, 
	actor_id BIGINT, 
	reaction VARCHAR(64) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	CONSTRAINT pk_telegram_reactions PRIMARY KEY (id), 
	CONSTRAINT fk_telegram_reactions_telegram_message_id_telegram_messages FOREIGN KEY(telegram_message_id) REFERENCES telegram_messages (id) ON DELETE CASCADE
)

;

