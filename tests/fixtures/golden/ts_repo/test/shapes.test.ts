import { expect, it } from "vitest";
import { Square, totalArea } from "../src/shapes";

it("sums areas", () => {
  expect(totalArea([new Square(2)])).toBe(4);
});
