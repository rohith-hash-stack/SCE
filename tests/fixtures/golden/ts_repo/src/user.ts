/** Committed version: positional parameters. The working-tree overlay replaces them (V2). */
export const fetchUser = async (id: string, retries: number): Promise<string> => {
  return `${id}:${retries}`;
};
