import { describe, expect, it } from "vitest";
import { bendLabel, techniqueMark } from "./tabNotation";

describe("bend notation", () => {
  it("writes bends in guitar notation (half-steps)", () => {
    expect(bendLabel(1)).toBe("b½");
    expect(bendLabel(2)).toBe("b1");
    expect(bendLabel(3)).toBe("b1½");
    expect(bendLabel(0.5)).toBe("b¼"); // quarter-tone curl
  });

  it("marks techniques", () => {
    expect(techniqueMark({ string: 0, fret: 7, technique: "bend", bendSemitones: 2 })).toBe("b1");
    expect(techniqueMark({ string: 0, fret: 7, technique: "bend" })).toBe("b");
    expect(techniqueMark({ string: 0, fret: 7, technique: "vibrato" })).toBe("~");
    expect(techniqueMark({ string: 0, fret: 7 })).toBeNull();
  });
});

import { bendArrowLabel } from "./tabNotation";

describe("bend arrow labels", () => {
  it("uses engraved tab conventions", () => {
    expect(bendArrowLabel(1)).toBe("½");
    expect(bendArrowLabel(2)).toBe("full");
    expect(bendArrowLabel(3)).toBe("1½");
    expect(bendArrowLabel(0.5)).toBe("¼");
    expect(bendArrowLabel(4)).toBe("2");
  });
});
