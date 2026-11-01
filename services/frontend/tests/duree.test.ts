import { describe, expect, it } from "vitest";

import ar from "@/dico/ar.json";
import fr from "@/dico/fr.json";
import { formaterDuree } from "@/lib/chat/duree";

describe("formatage des durées", () => {
  it("secondes seules sous la minute", () => {
    expect(formaterDuree(0, fr)).toBe("0 s");
    expect(formaterDuree(45_000, fr)).toBe("45 s");
    expect(formaterDuree(45_000, ar)).toBe("45 ث");
  });

  it("minutes et secondes au-delà", () => {
    expect(formaterDuree(125_000, fr)).toBe("2 min 05 s");
    expect(formaterDuree(125_000, ar)).toBe("2 د 05 ث");
  });

  it("ne descend jamais sous zéro", () => {
    expect(formaterDuree(-5, fr)).toBe("0 s");
  });
});
