-- SIQ 认证系统数据库表
-- 创建时间：2026-06-03

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('super_admin', 'admin', 'analyst', 'reviewer', 'viewer')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP,
    CONSTRAINT username_length CHECK (LENGTH(username) >= 3),
    CONSTRAINT email_format CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(255) NOT NULL,
    details JSONB,
    ip_address VARCHAR(45),
    user_agent VARCHAR(500),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 审计日志索引
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);

-- 报告审核表
CREATE TABLE IF NOT EXISTS report_reviews (
    id SERIAL PRIMARY KEY,
    report_path VARCHAR(500) NOT NULL,
    company_id VARCHAR(100) NOT NULL,
    report_year INTEGER NOT NULL,
    report_type VARCHAR(50) NOT NULL CHECK (report_type IN ('analysis', 'factcheck', 'tracking', 'legal')),
    reviewer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'revision_required')),
    review_result JSONB,
    reviewed_at TIMESTAMP,
    generated_by VARCHAR(100) NOT NULL,
    generated_at TIMESTAMP NOT NULL,
    version INTEGER DEFAULT 1,
    content_hash VARCHAR(64) NOT NULL,
    signature VARCHAR(500),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 报告审核索引
CREATE INDEX IF NOT EXISTS idx_review_company ON report_reviews(company_id);
CREATE INDEX IF NOT EXISTS idx_review_status ON report_reviews(status);
CREATE INDEX IF NOT EXISTS idx_review_reviewer ON report_reviews(reviewer_id);
CREATE INDEX IF NOT EXISTS idx_review_path ON report_reviews(report_path);

-- 创建更新时间触发器
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_report_reviews_updated_at BEFORE UPDATE ON report_reviews
FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 插入初始数据说明
COMMENT ON TABLE users IS '用户表：存储系统所有用户信息和角色';
COMMENT ON TABLE audit_logs IS '审计日志表：记录所有敏感操作';
COMMENT ON TABLE report_reviews IS '报告审核表：记录报告审核流程和状态';

-- 打印创建成功信息
DO $$
BEGIN
    RAISE NOTICE '✅ 数据库表创建成功';
    RAISE NOTICE '   - users: 用户表';
    RAISE NOTICE '   - audit_logs: 审计日志表';
    RAISE NOTICE '   - report_reviews: 报告审核表';
END $$;
