import { registerPlugin } from '@capacitor/core'

import type { MeetingCapturePlugin } from './definitions'

export * from './definitions'

export const MeetingCapture = registerPlugin<MeetingCapturePlugin>('MeetingCapture')
