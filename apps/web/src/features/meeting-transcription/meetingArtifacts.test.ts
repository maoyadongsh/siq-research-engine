/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  parseMeetingMinutes,
  parseSpeakerMergeSuggestions,
  selectLatestMinutesArtifact,
  selectPreferredMinutesArtifact,
} from './meetingArtifacts'
import type { MeetingArtifact } from './types'
import { meetingPostprocessStateLabel, meetingPostprocessStateTone } from './formatters'

function artifact(
  artifact_type: 'rolling_minutes' | 'final_minutes',
  version: number,
  state: string,
  content_json?: Record<string, unknown>,
): MeetingArtifact {
  return {
    id: `${artifact_type}-${version}`,
    meeting_id: 'meeting-1',
    artifact_type,
    version,
    state,
    content_json,
  }
}

test('preferred minutes use final content before a newer rolling artifact', () => {
  const values = [
    artifact('rolling_minutes', 9, 'ready', { overview: 'rolling' }),
    artifact('final_minutes', 2, 'ready', { overview: 'final' }),
  ]

  assert.equal(selectPreferredMinutesArtifact(values)?.id, 'final_minutes-2')
})

test('preferred minutes keep the last materialized version visible during regeneration', () => {
  const values = [
    artifact('final_minutes', 4, 'stale', { overview: 'reviewable version' }),
    artifact('final_minutes', 5, 'generating'),
  ]

  assert.equal(selectPreferredMinutesArtifact(values)?.id, 'final_minutes-4')
  assert.equal(selectLatestMinutesArtifact(values)?.id, 'final_minutes-5')
})

test('minutes parser reads every section from the one structured artifact and cleans evidence ids', () => {
  const parsed = parseMeetingMinutes({
    overview: '  会议摘要  ',
    decisions: [{ text: '采用 A 方案', source_segment_ids: ['segment-1', 'segment-1', ''] }],
    action_items: [{ text: '提交报告', owner: '张三', due_date: '2026-07-20', status: 'confirmed', source_segment_ids: ['segment-2'] }],
    speaker_viewpoints: [{ text: '关注现金流', speaker: '李四', source_segment_ids: ['segment-3'] }],
    keywords: [{ text: '客户留存率', source_segment_ids: ['segment-4'] }],
  })

  assert.equal(parsed.overview, '会议摘要')
  assert.deepEqual(parsed.decisions[0].source_segment_ids, ['segment-1'])
  assert.equal(parsed.action_items[0].owner, '张三')
  assert.equal(parsed.speaker_viewpoints[0].speaker, '李四')
  assert.equal(parsed.keywords[0].text, '客户留存率')
  assert.equal(parsed.agenda_topics.length, 0)
})

test('postprocess labels match the backend state machine', () => {
  assert.deepEqual(
    ['not_started', 'queued', 'running', 'succeeded', 'failed'].map(meetingPostprocessStateLabel),
    ['未开始', '排队中', '处理中', '已完成', '处理失败'],
  )
  assert.equal(meetingPostprocessStateTone('succeeded'), 'success')
  assert.equal(meetingPostprocessStateTone('failed'), 'error')
})

test('speaker merge suggestions use the latest recluster artifact and reject stale or unsafe entries', () => {
  const artifacts: MeetingArtifact[] = [
    {
      id: 'recluster-1',
      meeting_id: 'meeting-1',
      artifact_type: 'speaker_recluster',
      version: 1,
      state: 'ready',
      content_json: {
        global_embedding_recluster: {
          proposals: [{ source_track_ids: ['old-source'], target_track_id: 'old-target', score: 0.99, auto_apply: false }],
        },
      },
    },
    {
      id: 'recluster-2',
      meeting_id: 'meeting-1',
      artifact_type: 'speaker_recluster',
      version: 2,
      state: 'ready',
      content_json: {
        global_embedding_recluster: {
          proposals: [
            { source_track_ids: ['source-b'], target_track_id: 'target', score: 0.91, auto_apply: false, reason_code: 'LOW_TOP2_MARGIN' },
            { source_track_ids: ['source-a', 'source-a', 'target'], target_track_id: 'target', score: 0.96, auto_apply: false, reason_code: 'POLICY_NOT_VALIDATED' },
            { source_track_ids: ['already-merged'], target_track_id: 'target', score: 0.98, auto_apply: true, reason_code: 'AUTO_MERGE' },
            { source_track_ids: ['source-c'], target_track_id: 'target', score: 0.84, auto_apply: false, reason_code: 'SCORE_BELOW_AUTO_THRESHOLD' },
            { source_track_ids: ['missing'], target_track_id: 'target', score: 0.95, auto_apply: false },
            { source_track_ids: ['source-a'], target_track_id: 'target', score: 2, auto_apply: false },
          ],
        },
      },
    },
  ]

  assert.deepEqual(
    parseSpeakerMergeSuggestions(artifacts, new Set(['target', 'source-a', 'source-b', 'source-c'])),
    [
      { source_track_ids: ['source-a'], target_track_id: 'target', score: 0.96, reason_code: 'POLICY_NOT_VALIDATED' },
      { source_track_ids: ['source-b'], target_track_id: 'target', score: 0.91, reason_code: 'LOW_TOP2_MARGIN' },
    ],
  )
})
