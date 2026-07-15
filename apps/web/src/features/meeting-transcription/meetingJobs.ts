import type { MeetingJob } from './types'

export function latestMeetingJobsByType(jobs: MeetingJob[]): MeetingJob[] {
  const latest = new Map<string, MeetingJob>()
  for (const job of jobs) {
    const existing = latest.get(job.job_type)
    const jobTime = Date.parse(job.updated_at || '') || 0
    const existingTime = Date.parse(existing?.updated_at || '') || 0
    if (!existing || jobTime > existingTime) latest.set(job.job_type, job)
  }
  return [...latest.values()].sort((left, right) => {
    const rightTime = Date.parse(right.updated_at || '') || 0
    const leftTime = Date.parse(left.updated_at || '') || 0
    return rightTime - leftTime
  })
}
