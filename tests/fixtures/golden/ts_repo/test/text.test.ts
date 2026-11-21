import { describe, expect, it } from "vitest";
import { clean, countWords } from "../src/text";

describe("text", () => {
  it("cleans", () => {
    expect(clean("  A ")).toBe("a");
  });
  it("counts words", async () => {
    expect(await countWords("a b")).toBe(2);
  });
});
