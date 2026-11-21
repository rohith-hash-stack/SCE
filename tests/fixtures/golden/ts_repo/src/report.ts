import * as text from "./text";
import greet from "./greet";
import { sum } from "./index";

/** A one-line report: a namespace call, a default import, a barrel import. */
export function report(name: string, xs: number[]): string {
  return `${greet(text.clean(name))}: ${sum(xs)}`;
}
