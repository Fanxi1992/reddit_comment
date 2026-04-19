import type React from 'react'

type IconProps = {
  className?: string
}

function SvgIcon({ className = 'h-4 w-4', children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      {children}
    </svg>
  )
}

export function PlusIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 5v14M5 12h14" />
    </SvgIcon>
  )
}

export function TrashIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15M10 11v6M14 11v6" />
    </SvgIcon>
  )
}

export function UploadIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 16V4M7 9l5-5 5 5M5 20h14" />
    </SvgIcon>
  )
}

export function DownloadIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 4v12M7 11l5 5 5-5M5 20h14" />
    </SvgIcon>
  )
}

export function PlayIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="m8 5 11 7-11 7V5Z" />
    </SvgIcon>
  )
}

export function StopIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M6 6h12v12H6z" />
    </SvgIcon>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="m5 12 4 4L19 6" />
    </SvgIcon>
  )
}

export function AlertIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 9v4M12 17h.01M10 3h4l8 18H2L10 3Z" />
    </SvgIcon>
  )
}

export function ClockIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </SvgIcon>
  )
}

export function SparkIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3ZM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />
    </SvgIcon>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M8 8h11v11H8z" />
      <path d="M5 16H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1" />
    </SvgIcon>
  )
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <SvgIcon {...props}>
      <path d="m6 9 6 6 6-6" />
    </SvgIcon>
  )
}
