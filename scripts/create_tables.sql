-- -----------------------------
-- Required Extensions
-- -----------------------------
-- UUIDs for primary keys
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- vector support for embeddings (requires pgvector installed)
CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------
-- Conversation Header Table
-- -----------------------------
-- Stores high-level information about each completed conversation
CREATE TABLE completed_conversations (
    conversation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    final_status TEXT,
    review_score SMALLINT,
    operator_id TEXT,
    customer_id TEXT
);

-- -----------------------------
-- Conversation Messages Table
-- -----------------------------
-- Stores individual messages per conversation, including metadata
CREATE TABLE completed_messages (
    message_id SERIAL PRIMARY KEY,
    conversation_id UUID REFERENCES completed_conversations(conversation_id) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,
    sender TEXT,                       -- e.g., 'USER' or 'AGENT'
    text_content TEXT,                 -- raw text
    intent_label TEXT,                 -- optional: user's intent per message
    sentiment_label TEXT,              -- optional: sentiment analysis result
    entities JSONB,                    -- optional: named entities extracted
    agent_action JSONB                 -- optional: internal agent action log
);

-- Index for fast lookups of messages by conversation
CREATE INDEX idx_completed_messages_conversation_id
ON completed_messages(conversation_id);

-- -----------------------------
-- Knowledge Base - Orders
-- -----------------------------
-- Stores order information in a denormalized way for agents
CREATE TABLE orders (
    order_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    customer_email TEXT,
    status TEXT,                        -- e.g., 'PENDING', 'SHIPPED', 'DELIVERED'
    items JSONB                          -- JSON array of order items
);

-- Index for quick lookup by customer email
CREATE INDEX idx_orders_customer_email
ON orders(customer_email);

-- -----------------------------
-- Knowledge Base - Returns
-- -----------------------------
-- Stores return requests with denormalized info for agent logic
CREATE TABLE returns (
    return_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID,                       -- link to orders
    customer_email TEXT,                  -- denormalized for quick lookup
    item_details JSONB,                   -- JSON object for returned item
    status TEXT                           -- e.g., 'REQUESTED', 'APPROVED', 'REJECTED'
);

-- Indexes for fast lookups
CREATE INDEX idx_returns_order_id ON returns(order_id);
CREATE INDEX idx_returns_customer_email ON returns(customer_email);

-- -----------------------------
-- Knowledge Base - Articles
-- -----------------------------
-- Stores text chunks for agents with vector embeddings
CREATE TABLE kb_articles (
    chunk_id SERIAL PRIMARY KEY,
    text_chunk TEXT,                      -- small paragraph used as context
    category TEXT,                         -- e.g., 'RETURNS', 'SHIPPING', 'ONBOARDING'
    source_file TEXT,                      -- e.g., returns.md
    embedding vector(768)                  -- pgvector embedding for similarity search
);

-- Index category for filtered search
CREATE INDEX idx_kb_articles_category
ON kb_articles(category);

-- Vector similarity index for embedding search
-- Uncomment after adding data to kb_articles
-- CREATE INDEX idx_kb_articles_embedding
-- ON kb_articles
-- USING ivfflat (embedding vector_cosine_ops);
