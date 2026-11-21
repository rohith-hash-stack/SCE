/** Trim and lower-case a string. */
export function clean(s: string): string {
  return s.trim().toLowerCase();
}

/** Split a cleaned string into words. */
export const words = (s: string): string[] => clean(s).split(/\s+/).filter(Boolean);

/** Count the words of a string, after a tick. */
export const countWords = async (s: string): Promise<number> => {
  await Promise.resolve();
  return words(s).length;
};
