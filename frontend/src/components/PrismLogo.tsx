import React from 'react'

export type PrismLogoSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | number

export interface PrismLogoProps
  extends Omit<React.ImgHTMLAttributes<HTMLImageElement>, 'src' | 'alt' | 'width' | 'height'> {
  size?: PrismLogoSize
  className?: string
  alt?: string
}

// Preset sizing matching the established layout rhythm across PRism surfaces:
// xs: 14px (compact dropdown actions / sub-badges)
// sm: 20px (footer & compact marks)
// md: 28px (workspace header, repository picker)
// lg: 32px (landing page navigation bar)
// xl: 40px (OAuth callback and auth screens)
const SIZE_CLASSES: Record<Exclude<PrismLogoSize, number>, string> = {
  xs: 'w-3.5 h-3.5',
  sm: 'w-5 h-5',
  md: 'w-7 h-7',
  lg: 'w-8 h-8',
  xl: 'w-10 h-10',
}

export const CANONICAL_LOGO_PATH = '/prism-logo-github-mono.svg'
export const FALLBACK_LOGO_PATH = '/prism-logo-github-mono.png'

export const PrismLogo: React.FC<PrismLogoProps> = ({
  size = 'md',
  className = '',
  alt = 'PRism',
  ...rest
}) => {
  const isNamedSize = typeof size === 'string'
  const sizeClass = isNamedSize ? SIZE_CLASSES[size] || SIZE_CLASSES.md : undefined
  const inlineSize = typeof size === 'number' ? { width: `${size}px`, height: `${size}px` } : undefined

  return (
    <img
      src={CANONICAL_LOGO_PATH}
      alt={alt}
      style={{ ...inlineSize, ...rest.style }}
      className={`aspect-square shrink-0 select-none object-contain ${sizeClass || ''} ${className}`.trim()}
      {...rest}
    />
  )
}

export default PrismLogo
