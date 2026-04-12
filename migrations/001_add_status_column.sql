-- migrations/001_add_status_column.sql
-- Database migration for pipelined architecture
-- This adds status tracking, file path columns, and retry queue support

-- Add status column to jobs table for queue-based job tracking
ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'pending_discover';

-- Create index for status-based queries
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Add updated_at column for tracking when jobs were last modified
ALTER TABLE jobs ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Add tailored_path column for storing paths to tailored resumes
ALTER TABLE jobs ADD COLUMN tailored_path TEXT;

-- Add cover_path column for storing paths to cover letters
ALTER TABLE jobs ADD COLUMN cover_path TEXT;

-- Add applied_at column for tracking when applications were submitted
ALTER TABLE jobs ADD COLUMN applied_at TIMESTAMP;

-- Create retry queue table for failed jobs
CREATE TABLE IF NOT EXISTS retry_queue (
    job_url TEXT PRIMARY KEY,
    stage TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
