/** The D1 trap in JS, part 1: a class method; no module function has this name. */
export class Cache {
  entries = new Map<string, unknown>();

  setEntry(key: string, value: unknown): void {
    this.entries.set(key, value);
  }
}
