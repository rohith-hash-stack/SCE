/** Sum of numbers. */
export function sum(xs: number[]): number {
  return xs.reduce((a, b) => a + b, 0);
}

/** Mean of numbers; throws on an empty list. */
export function mean(xs: number[]): number {
  if (xs.length === 0) {
    throw new RangeError("empty");
  }
  return sum(xs) / xs.length;
}
