export type GreetingKey = 'morning' | 'afternoon' | 'evening'

export function greetingKey(date: Date): GreetingKey {
  const hour = date.getHours()
  if (hour < 12) return 'morning'
  if (hour < 18) return 'afternoon'
  return 'evening'
}
