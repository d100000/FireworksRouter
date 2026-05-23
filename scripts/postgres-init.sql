-- ============================================================================
-- PostgreSQL 首次启动优化（docker-compose 自动执行一次）
-- 主要做两件事：
--   1) 启用 pg_stat_statements 扩展（慢查询追踪）
--   2) 业务索引由 Alembic 迁移负责，这里不重复
-- ============================================================================

-- 启用查询统计扩展
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 设置时区为 UTC（与应用层一致）
ALTER DATABASE fwr SET timezone TO 'UTC';
