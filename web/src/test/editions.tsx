import type { ReactNode } from "react";
import { StaticEdition } from "../edition";

export { CODE, FULL, LITE } from "./editionFeatures";

export function edition(features: string[], id: "lite" | "code" | "full" = "full") {
  return ({ children }: { children: ReactNode }) => <StaticEdition id={id} features={features}>{children}</StaticEdition>;
}
