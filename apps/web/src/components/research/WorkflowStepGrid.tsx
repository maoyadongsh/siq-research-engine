import { ArrowRight, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router-dom'

export interface WorkflowStep {
  to: string
  icon: LucideIcon
  label: string
  desc: string
}

interface WorkflowStepGridProps {
  steps: readonly WorkflowStep[]
}

export function WorkflowStepGrid({ steps }: WorkflowStepGridProps) {
  return (
    <section className="workflow-step-grid grid grid-cols-2 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
      {steps.map((step, index) => (
        <Link
          key={step.to}
          to={step.to}
          className="workflow-step-card premium-card group relative flex min-h-[118px] min-w-0 flex-col overflow-hidden p-3 text-left transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-primary/25 sm:min-h-[160px] sm:p-5 sm:text-center"
        >
          <span
            className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-primary/40 via-primary/20 to-transparent sm:h-1.5"
            aria-hidden="true"
          />
          <span
            className="workflow-step-index pointer-events-none absolute right-3 top-3 z-10 grid size-7 place-items-center rounded-full border border-border bg-card text-xs font-bold leading-none text-text-muted shadow-sm tabular-nums"
            aria-hidden="true"
          >
            {index + 1}
          </span>
          <span className="sr-only">第 {index + 1} 步：</span>
          <div className="premium-icon h-9 w-9 shrink-0 rounded-xl transition-colors group-hover:text-primary-dark sm:mx-auto sm:h-12 sm:w-12 sm:rounded-2xl">
            <step.icon className="h-5 w-5 sm:h-6 sm:w-6" aria-hidden="true" />
          </div>
          <p className="mt-3 pr-8 text-sm font-bold leading-tight text-text sm:mt-4 sm:pr-0 sm:text-base">{step.label}</p>
          <p className="mt-1 line-clamp-2 text-xs leading-5 text-text-muted sm:mt-1.5 sm:text-sm sm:leading-relaxed">{step.desc}</p>
          <ArrowRight
            className="mt-auto h-5 w-5 shrink-0 pt-3 text-text-muted opacity-0 transition-[transform,color,opacity] duration-200 group-hover:translate-x-1 group-hover:text-primary group-hover:opacity-100 sm:mx-auto sm:pt-4"
            aria-hidden="true"
          />
        </Link>
      ))}
    </section>
  )
}
