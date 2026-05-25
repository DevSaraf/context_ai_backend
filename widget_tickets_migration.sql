-- Run this on BOTH local Docker DB and Azure DB before deploying

CREATE TABLE IF NOT EXISTS widget_tickets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    company_id VARCHAR(255),
    customer_name VARCHAR(200),
    customer_email VARCHAR(200),
    subject VARCHAR(500),
    message TEXT,
    ai_response TEXT,
    confidence FLOAT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'pending',  -- pending, auto_responded, reviewed, closed
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_widget_tickets_user ON widget_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_widget_tickets_status ON widget_tickets(status);