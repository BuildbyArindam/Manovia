/**
 * Compile-time contract between the hand-written client types and the API.
 *
 * `api-schema.d.ts` is generated from `docs/openapi.json` (`npm run api:types`),
 * which the backend exports from the running app and a backend test keeps from
 * drifting. Each line below says "everything the backend promises on the wire
 * can be used where the client expects this shape". If the backend renames,
 * retypes or drops a field the client reads, the wire type stops being
 * assignable and `tsc --noEmit` fails, so API drift breaks CI instead of a
 * person's screen.
 *
 * This file has no runtime behaviour and is not imported by the app.
 */

import type { components } from "@/lib/api-schema";
import type { SessionLike } from "@/lib/api";
import type {
  AssessmentItem,
  InstrumentSummary,
  ResponseOption,
} from "@/features/assessments/types";

type Schemas = components["schemas"];

/** `true` when every `Wire` value is a valid `Client` value. */
type Satisfies<Wire, Client> = [Wire] extends [Client] ? true : false;

/** Compile error unless `T` is `true`. */
type Expect<T extends true> = T;

export type ApiContract = [
  Expect<Satisfies<Schemas["SessionOut"], SessionLike>>,
  Expect<Satisfies<Schemas["InstrumentSummary"], InstrumentSummary>>,
  Expect<Satisfies<Schemas["ResponseOptionView"], ResponseOption>>,
  Expect<Satisfies<Schemas["ItemView"], AssessmentItem>>,
];
