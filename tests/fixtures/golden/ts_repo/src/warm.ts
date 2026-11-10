/** The D1 trap in JS, part 2: a bare call to setEntry, nothing imported (a ReferenceError at runtime). */
export function warm(): void {
  // @ts-expect-error: deliberately undefined here
  setEntry("ready", true);
}
