import { fetchUser } from "./user";

/** An unchanged caller of fetchUser (positional arguments). */
export async function loadProfile(id: string): Promise<string> {
  return fetchUser(id, 2);
}
