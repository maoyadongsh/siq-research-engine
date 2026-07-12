import { createRequestScope } from '../../shared/async/requestScope'

export type {
  RequestScope as TaskRequestScope,
  RequestScopeToken as TaskRequestToken,
} from '../../shared/async/requestScope'

export const createTaskRequestScope = createRequestScope
