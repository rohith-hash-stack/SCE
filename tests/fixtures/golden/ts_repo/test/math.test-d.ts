import { expectTypeOf, test } from "vitest";
import { mean } from "../src/math";

test("mean returns a number", () => {
  expectTypeOf(mean).returns.toBeNumber();
});
