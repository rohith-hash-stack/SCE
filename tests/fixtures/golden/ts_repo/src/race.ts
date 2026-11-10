/** The D2 trap in JS: unresolved calls embedded in a return and in Promise.race (pilot B-02's shape). */
export async function fetchOne(url: string): Promise<string> {
  return url;
}

export function firstOf(url: string, ms: number): Promise<string> {
  // @ts-expect-error: timeoutAfter is defined nowhere
  return Promise.race([fetchOne(url), timeoutAfter(ms)]);
}

export function wrapped(x: string): string {
  // @ts-expect-error: undefinedThing is defined nowhere
  return String(undefinedThing(x));
}
