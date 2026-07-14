const DATABASE_NAME = 'siq-meeting-audio-outbox-v1'
const DATABASE_VERSION = 1
const STORE_NAME = 'records'
const MEETING_EPOCH_INDEX = 'meetingEpoch'
const DEFAULT_TTL_MS = 10 * 60 * 1000

interface MeetingOutboxFrameRecord {
  key: string
  kind: 'frame'
  meetingEpoch: string
  meetingId: string
  streamEpoch: number
  sequence: number
  frame: ArrayBuffer
  expiresAt: number
}

interface MeetingOutboxMetaRecord {
  key: string
  kind: 'meta'
  meetingEpoch: string
  meetingId: string
  streamEpoch: number
  clientStreamId: string
  lastAckedSequence: number
  expiresAt: number
}

type MeetingOutboxRecord = MeetingOutboxFrameRecord | MeetingOutboxMetaRecord

export interface MeetingOutboxSnapshot {
  clientStreamId: string | null
  lastAckedSequence: number
  frames: Map<number, ArrayBuffer>
}

export interface MeetingOutboxStore {
  restore(meetingId: string, streamEpoch: number): Promise<MeetingOutboxSnapshot>
  putFrame(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    lastAckedSequence: number,
    sequence: number,
    frame: ArrayBuffer,
  ): Promise<void>
  acknowledge(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    ackSequence: number,
  ): Promise<void>
  clear(meetingId: string, streamEpoch: number): Promise<void>
}

function meetingEpochKey(meetingId: string, streamEpoch: number) {
  return `${meetingId}:${streamEpoch}`
}

function metaKey(meetingEpoch: string) {
  return `meta:${meetingEpoch}`
}

function frameKey(meetingEpoch: string, sequence: number) {
  return `frame:${meetingEpoch}:${String(sequence).padStart(16, '0')}`
}

function requestResult<T>(request: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error || new Error('IndexedDB request failed'))
  })
}

function transactionComplete(transaction: IDBTransaction) {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted'))
    transaction.onerror = () => reject(transaction.error || new Error('IndexedDB transaction failed'))
  })
}

export class IndexedDbMeetingOutboxStore implements MeetingOutboxStore {
  private readonly ttlMs: number
  private databasePromise: Promise<IDBDatabase> | null = null

  constructor(ttlMs = DEFAULT_TTL_MS) {
    this.ttlMs = ttlMs
  }

  private database() {
    if (!globalThis.indexedDB) return Promise.reject(new Error('IndexedDB is unavailable'))
    if (!this.databasePromise) {
      this.databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
        const request = globalThis.indexedDB.open(DATABASE_NAME, DATABASE_VERSION)
        request.onupgradeneeded = () => {
          const database = request.result
          const store = database.objectStoreNames.contains(STORE_NAME)
            ? request.transaction?.objectStore(STORE_NAME)
            : database.createObjectStore(STORE_NAME, { keyPath: 'key' })
          if (store && !store.indexNames.contains(MEETING_EPOCH_INDEX)) {
            store.createIndex(MEETING_EPOCH_INDEX, 'meetingEpoch', { unique: false })
          }
        }
        request.onsuccess = () => {
          request.result.onversionchange = () => request.result.close()
          resolve(request.result)
        }
        request.onerror = () => reject(request.error || new Error('Unable to open meeting audio outbox'))
        request.onblocked = () => reject(new Error('Meeting audio outbox upgrade is blocked'))
      })
    }
    return this.databasePromise
  }

  async restore(meetingId: string, streamEpoch: number): Promise<MeetingOutboxSnapshot> {
    const database = await this.database()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const records = await requestResult(
      store.index(MEETING_EPOCH_INDEX).getAll(IDBKeyRange.only(meetingEpoch)),
    ) as MeetingOutboxRecord[]
    const now = Date.now()
    const frames = new Map<number, ArrayBuffer>()
    let meta: MeetingOutboxMetaRecord | null = null
    for (const record of records) {
      if (record.expiresAt <= now) {
        store.delete(record.key)
      } else if (record.kind === 'meta') {
        meta = record
      } else if (record.sequence > (meta?.lastAckedSequence ?? -1)) {
        frames.set(record.sequence, record.frame)
      }
    }
    if (meta) {
      for (const [sequence] of frames) {
        if (sequence <= meta.lastAckedSequence) frames.delete(sequence)
      }
    }
    await transactionComplete(transaction)
    return {
      clientStreamId: meta?.clientStreamId || null,
      lastAckedSequence: meta?.lastAckedSequence ?? -1,
      frames,
    }
  }

  async putFrame(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    lastAckedSequence: number,
    sequence: number,
    frame: ArrayBuffer,
  ) {
    const database = await this.database()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const expiresAt = Date.now() + this.ttlMs
    store.put({
      key: metaKey(meetingEpoch),
      kind: 'meta',
      meetingEpoch,
      meetingId,
      streamEpoch,
      clientStreamId,
      lastAckedSequence,
      expiresAt,
    } satisfies MeetingOutboxMetaRecord)
    store.put({
      key: frameKey(meetingEpoch, sequence),
      kind: 'frame',
      meetingEpoch,
      meetingId,
      streamEpoch,
      sequence,
      frame,
      expiresAt,
    } satisfies MeetingOutboxFrameRecord)
    await transactionComplete(transaction)
  }

  async acknowledge(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    ackSequence: number,
  ) {
    const database = await this.database()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const records = await requestResult(
      store.index(MEETING_EPOCH_INDEX).getAll(IDBKeyRange.only(meetingEpoch)),
    ) as MeetingOutboxRecord[]
    const expiresAt = Date.now() + this.ttlMs
    for (const record of records) {
      if (record.kind === 'frame' && record.sequence <= ackSequence) store.delete(record.key)
    }
    store.put({
      key: metaKey(meetingEpoch),
      kind: 'meta',
      meetingEpoch,
      meetingId,
      streamEpoch,
      clientStreamId,
      lastAckedSequence: ackSequence,
      expiresAt,
    } satisfies MeetingOutboxMetaRecord)
    await transactionComplete(transaction)
  }

  async clear(meetingId: string, streamEpoch: number) {
    const database = await this.database()
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    const store = transaction.objectStore(STORE_NAME)
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const keys = await requestResult(
      store.index(MEETING_EPOCH_INDEX).getAllKeys(IDBKeyRange.only(meetingEpoch)),
    )
    keys.forEach((key) => store.delete(key))
    await transactionComplete(transaction)
  }
}

export class MemoryMeetingOutboxStore implements MeetingOutboxStore {
  private readonly records: Map<string, MeetingOutboxRecord>
  private readonly ttlMs: number

  constructor(records = new Map<string, MeetingOutboxRecord>(), ttlMs = DEFAULT_TTL_MS) {
    this.records = records
    this.ttlMs = ttlMs
  }

  async restore(meetingId: string, streamEpoch: number): Promise<MeetingOutboxSnapshot> {
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const now = Date.now()
    const frames = new Map<number, ArrayBuffer>()
    let meta: MeetingOutboxMetaRecord | null = null
    for (const [key, record] of this.records) {
      if (record.expiresAt <= now) {
        this.records.delete(key)
      } else if (record.meetingEpoch === meetingEpoch) {
        if (record.kind === 'meta') meta = record
        else frames.set(record.sequence, record.frame)
      }
    }
    for (const [sequence] of frames) {
      if (sequence <= (meta?.lastAckedSequence ?? -1)) frames.delete(sequence)
    }
    return {
      clientStreamId: meta?.clientStreamId || null,
      lastAckedSequence: meta?.lastAckedSequence ?? -1,
      frames,
    }
  }

  async putFrame(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    lastAckedSequence: number,
    sequence: number,
    frame: ArrayBuffer,
  ) {
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    const expiresAt = Date.now() + this.ttlMs
    this.records.set(metaKey(meetingEpoch), {
      key: metaKey(meetingEpoch),
      kind: 'meta',
      meetingEpoch,
      meetingId,
      streamEpoch,
      clientStreamId,
      lastAckedSequence,
      expiresAt,
    })
    this.records.set(frameKey(meetingEpoch, sequence), {
      key: frameKey(meetingEpoch, sequence),
      kind: 'frame',
      meetingEpoch,
      meetingId,
      streamEpoch,
      sequence,
      frame,
      expiresAt,
    })
  }

  async acknowledge(
    meetingId: string,
    streamEpoch: number,
    clientStreamId: string,
    ackSequence: number,
  ) {
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    for (const [key, record] of this.records) {
      if (record.meetingEpoch === meetingEpoch && record.kind === 'frame' && record.sequence <= ackSequence) {
        this.records.delete(key)
      }
    }
    this.records.set(metaKey(meetingEpoch), {
      key: metaKey(meetingEpoch),
      kind: 'meta',
      meetingEpoch,
      meetingId,
      streamEpoch,
      clientStreamId,
      lastAckedSequence: ackSequence,
      expiresAt: Date.now() + this.ttlMs,
    })
  }

  async clear(meetingId: string, streamEpoch: number) {
    const meetingEpoch = meetingEpochKey(meetingId, streamEpoch)
    for (const [key, record] of this.records) {
      if (record.meetingEpoch === meetingEpoch) this.records.delete(key)
    }
  }
}

export function createMeetingOutboxStore(): MeetingOutboxStore {
  return globalThis.indexedDB
    ? new IndexedDbMeetingOutboxStore()
    : new MemoryMeetingOutboxStore()
}
