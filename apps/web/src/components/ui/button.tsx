import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-[var(--radius-control)] text-sm font-semibold whitespace-nowrap transition-[background,border-color,color,box-shadow,transform] outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(0,113,227,0.16)] hover:-translate-y-0.5 hover:bg-primary/90",
        primary: "bg-primary text-primary-foreground shadow-[0_10px_24px_rgba(0,113,227,0.16)] hover:-translate-y-0.5 hover:bg-primary/90",
        destructive:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40",
        danger:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40",
        outline:
          "border border-[#cbd8e8] bg-transparent text-foreground shadow-none hover:border-[#0071e3] hover:bg-transparent hover:text-[#005bb5]",
        secondary:
          "border border-[#cbd8e8] bg-transparent text-secondary-foreground shadow-none hover:border-[#0071e3] hover:bg-transparent hover:text-[#005bb5]",
        ghost:
          "bg-transparent hover:bg-transparent hover:text-[#005bb5]",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-8 gap-1.5 rounded-[var(--radius-control)] px-3 has-[>svg]:px-2.5",
        md: "h-10 px-4 py-2 has-[>svg]:px-3",
        lg: "h-11 rounded-[var(--radius-card)] px-6 has-[>svg]:px-4",
        icon: "size-10",
        "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-8",
        "icon-lg": "size-11",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  leftIcon,
  rightIcon,
  children,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
    leftIcon?: React.ReactNode
    rightIcon?: React.ReactNode
  }) {
  const Comp = asChild ? Slot.Root : "button"

  const content = asChild && (leftIcon || rightIcon)
    ? (
      <>
        {leftIcon}
        <Slot.Slottable>{children}</Slot.Slottable>
        {rightIcon}
      </>
    )
    : children

  if (asChild) {
    return (
      <Comp
        data-slot="button"
        data-variant={variant}
        data-size={size}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      >
        {content}
      </Comp>
    )
  }

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    >
      {leftIcon}
      {children}
      {rightIcon}
    </Comp>
  )
}

export { Button }
