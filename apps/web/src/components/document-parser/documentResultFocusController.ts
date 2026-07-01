import { useCallback, useEffect, useState } from 'react'
import type { FocusTarget } from './documentResultWorkbenchUtils'

export type DocumentResultFocusControllerState = {
  activePage: number
  focused: FocusTarget
  activeTab: string
}

export type DocumentResultFocusControllerAction =
  | { type: 'focus'; target: FocusTarget }
  | { type: 'selectPage'; page: number }
  | { type: 'setActiveTab'; tab: string }
  | { type: 'resetTask'; pageNumbers: number[] }

export function firstDocumentResultPage(pageNumbers: number[]) {
  return pageNumbers[0] || 1
}

export function documentResultFocusControllerReducer(
  state: DocumentResultFocusControllerState,
  action: DocumentResultFocusControllerAction,
): DocumentResultFocusControllerState {
  if (action.type === 'focus') {
    return {
      ...state,
      focused: action.target,
      activePage: action.target?.page || state.activePage,
    }
  }

  if (action.type === 'selectPage') {
    return {
      ...state,
      activePage: action.page,
      focused: { kind: 'page', id: `page-${action.page}`, page: action.page },
    }
  }

  if (action.type === 'setActiveTab') {
    return {
      ...state,
      activeTab: action.tab,
    }
  }

  return {
    ...state,
    activePage: firstDocumentResultPage(action.pageNumbers),
    focused: null,
  }
}

export function useDocumentResultFocusController({
  taskId,
  pageNumbers,
}: {
  taskId: string
  pageNumbers: number[]
}) {
  const [state, setState] = useState<DocumentResultFocusControllerState>(() => ({
    activePage: firstDocumentResultPage(pageNumbers),
    focused: null,
    activeTab: 'preview',
  }))

  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setState((current) => documentResultFocusControllerReducer(current, { type: 'resetTask', pageNumbers }))
    })
    return () => {
      cancelled = true
    }
  }, [taskId, pageNumbers])

  const focusTarget = useCallback((target: FocusTarget) => {
    setState((current) => documentResultFocusControllerReducer(current, { type: 'focus', target }))
  }, [])

  const selectPage = useCallback((page: number) => {
    setState((current) => documentResultFocusControllerReducer(current, { type: 'selectPage', page }))
  }, [])

  const setActiveTab = useCallback((tab: string) => {
    setState((current) => documentResultFocusControllerReducer(current, { type: 'setActiveTab', tab }))
  }, [])

  return {
    activePage: state.activePage,
    focused: state.focused,
    activeTab: state.activeTab,
    focusTarget,
    selectPage,
    setActiveTab,
  }
}
