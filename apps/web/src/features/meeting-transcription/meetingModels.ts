import type { MeetingModel } from './types'

export function preferredMeetingModel(models: MeetingModel[]) {
  const available = models.filter((model) => model.available && model.configured)
  return available.find((model) => model.is_default) ?? available[0]
}
