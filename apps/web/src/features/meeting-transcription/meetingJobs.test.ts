/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { latestMeetingJobsByType } from './meetingJobs'
import type { MeetingJob } from './types'

function job(id: string, jobType: string, state: string, updatedAt: string): MeetingJob {
  return { id, job_type: jobType, state, updated_at: updatedAt }
}

test('keeps only the latest job for each processing step', () => {
  const values = latestMeetingJobsByType([
    job('minutes-old', 'final_minutes', 'failed', '2026-07-15T01:00:00Z'),
    job('correction', 'correction', 'succeeded', '2026-07-15T02:00:00Z'),
    job('minutes-new', 'final_minutes', 'succeeded', '2026-07-15T03:00:00Z'),
  ])

  assert.deepEqual(values.map((item) => item.id), ['minutes-new', 'correction'])
})

test('uses input order when timestamps are absent', () => {
  const values = latestMeetingJobsByType([
    job('latest', 'correction', 'running', ''),
    job('older', 'correction', 'failed', ''),
  ])

  assert.deepEqual(values.map((item) => item.id), ['latest'])
})
