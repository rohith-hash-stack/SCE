/** Working-tree version: one object-destructured parameter replaces the positional ones; profile.ts is not updated. */
export const fetchUser = async ({ id, retries }: { id: string; retries: number }): Promise<string> => {
  return `${id}:${retries}`;
};
