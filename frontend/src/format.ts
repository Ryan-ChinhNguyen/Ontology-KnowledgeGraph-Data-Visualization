const SIZE_UNITS = ['B', 'KB', 'MB']

export function readableSize(bytes: number): string {
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < SIZE_UNITS.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${SIZE_UNITS[unit]}`
}

const RELATIVE_STEPS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 30],
  ['month', 12],
]

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "3 minutes ago" is read at a glance where a timestamp has to be decoded.
 *  The exact time stays available as the element's tooltip. */
export function relativeTime(iso: string): string {
  let amount = (Date.parse(iso) - Date.now()) / 1000
  for (const [unit, span] of RELATIVE_STEPS) {
    if (Math.abs(amount) < span) return relative.format(Math.round(amount), unit)
    amount /= span
  }
  return relative.format(Math.round(amount), 'year')
}

export function fullTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

/** Numbers line up on the decimal point when they are right-aligned, which
 *  makes a column of them comparable without reading every digit. */
export function isNumeric(inferredType: string): boolean {
  return /int|float|double|decimal|numeric|real/i.test(inferredType)
}
