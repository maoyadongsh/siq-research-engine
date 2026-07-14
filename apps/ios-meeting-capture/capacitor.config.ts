import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.siqresearch.meetingcapture',
  appName: 'SIQ Meeting Capture',
  webDir: '../web/dist',
  ios: {
    contentInset: 'automatic',
    scheme: 'SIQMeeting',
  },
}

export default config
