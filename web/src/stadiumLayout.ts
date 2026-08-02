import type { Seat, VenueLayoutKind } from "./types";

/**
 * Pure geometry: turns a flat seat list into polar (then cartesian) seat
 * positions arranged in curved rows around a central stage (theater — a
 * semicircle) or field (stadium — a full ring). No rendering here, just
 * numbers — SeatMap.tsx consumes this to position absolutely-placed seat
 * buttons. Kept separate from SeatMap so the trigonometry doesn't tangle
 * with interaction/state logic.
 *
 * Sections fall into two shapes, and conflating them was the source of an
 * earlier layout bug:
 *  - "band" sections are depth tiers — they span the FULL width (Orchestra,
 *    Balcony, Floor, Upper Bowl). A venue can have several, stacked front to
 *    back as concentric rings.
 *  - "wedge" sections are genuinely angular slices of ONE tier (Home Side,
 *    Away Side, North End, South End; the four Lower Bowl quadrants). These
 *    share a single ring and divide its angular width between them.
 * Theater sections are always bands (a stage-facing venue has no notion of
 * "east side" seating). For stadium, a section is a wedge if its name
 * mentions a side/direction (north/south/east/west/home/away) — this is a
 * naming convention for the small set of demo venues this app seeds, not a
 * general-purpose classifier.
 *
 * Consecutive wedge sections (in the order the API returns them) are
 * grouped into one ring; each band section gets its own ring. Rings are
 * placed at increasing radius in that order, each starting where the
 * previous one's outermost row ends (plus a gap) — never overlapping.
 *
 * Within a ring, radius sizing works in two passes conceptually (compute
 * the minimum radius the ring's front row needs so seats sit at least
 * SEAT_PITCH_PX apart, then lay out every row from there) because a fixed
 * angle-per-seat only avoids overlap if the radius is large enough that
 * physical arc length (radius × angle) is at least one seat-pitch wide —
 * skipping this sizing step is what caused the original overlap bug where
 * front-row seats were crushed together.
 */

const DEG2RAD = Math.PI / 180;
const SEAT_PITCH_PX = 26;
const SECTION_GAP_DEG = 5;
const ROW_GAP_PX = 32;
export const SEAT_BOX = 22;
const MIN_BASE_RADIUS = 60;
const WEDGE_KEYWORDS = ["north", "south", "east", "west", "home", "away"];

// Rough estimate of a rendered section-label pill's half-width at the
// 0.7rem semibold font this app uses, for the given section name — a label
// is a wide pill of arbitrary text ("Lower Bowl North" renders to ~120px),
// not a small dot, so the gap between rings needs to scale with the
// *longest* section name actually in this venue. A flat gap sized for
// worst-case stadium names (long, wedge-heavy) was wildly oversized for a
// two-word theater layout ("Orchestra"/"Balcony"), pushing simple venues
// into unnecessary horizontal scrolling. Calibrated empirically against
// this app's actual label CSS, not derived from font metrics.
function labelHalfWidthPx(name: string): number {
  return name.length * 2.6 + 10;
}

export interface PositionedSeat {
  seat: Seat;
  x: number;
  y: number;
}

export interface PositionedSection {
  name: string;
  seats: PositionedSeat[];
  labelX: number;
  labelY: number;
  // Bounds of this section's arc band, padded slightly beyond its seats —
  // consumed by sectionBandPath() to draw a visible colored region per
  // section, so it's obvious at a glance where one section ends and the
  // next begins instead of relying on the text label alone.
  innerRadius: number;
  outerRadius: number;
  startDeg: number;
  endDeg: number;
}

export interface VenueLayout {
  sections: PositionedSection[];
  width: number;
  height: number;
  centerX: number;
  centerY: number;
  centerRadius: number;
  centerLabel: string;
}

function groupRows(seats: Seat[]): Seat[][] {
  const byRow = new Map<string, Seat[]>();
  for (const seat of seats) {
    if (!byRow.has(seat.row_label)) byRow.set(seat.row_label, []);
    byRow.get(seat.row_label)!.push(seat);
  }
  return [...byRow.values()];
}

type SectionShape = "band" | "wedge";

function sectionShape(name: string, layout: VenueLayoutKind): SectionShape {
  if (layout === "theater") return "band";
  const lower = name.toLowerCase();
  return WEDGE_KEYWORDS.some((kw) => lower.includes(kw)) ? "wedge" : "band";
}

interface RingSection {
  name: string;
  rows: Seat[][];
  maxRowLen: number;
}

interface Ring {
  shape: SectionShape;
  sections: RingSection[];
}

function layOutRow(
  row: Seat[],
  radius: number,
  sectionCenterDeg: number,
  anglePerSeatDeg: number,
  fullCircle: boolean,
): PositionedSeat[] {
  const sorted = [...row].sort((a, b) => a.seat_number - b.seat_number);
  const n = sorted.length;
  return sorted.map((seat, seatIndex) => {
    const deg = fullCircle
      ? sectionCenterDeg + seatIndex * anglePerSeatDeg
      : sectionCenterDeg + (seatIndex - (n - 1) / 2) * anglePerSeatDeg;
    const rad = deg * DEG2RAD;
    return { seat, x: radius * Math.sin(rad), y: radius * Math.cos(rad) };
  });
}

/** SVG path for a section's arc band (an annulus segment), built by
 * sampling points along the inner and outer radius rather than using SVG's
 * arc command — sidesteps getting the large-arc/sweep flags backwards, and
 * looks smooth enough at these radii with one segment every few degrees. */
export function sectionBandPath(section: PositionedSection): string {
  const { innerRadius, outerRadius, startDeg, endDeg } = section;
  const span = endDeg - startDeg;
  const steps = Math.max(Math.round(Math.abs(span) / 6), 2);
  const point = (r: number, deg: number) => {
    const rad = deg * DEG2RAD;
    return `${r * Math.sin(rad)} ${r * Math.cos(rad)}`;
  };
  const outer = Array.from(
    { length: steps + 1 },
    (_, i) => `L ${point(outerRadius, startDeg + (span * i) / steps)}`,
  );
  const inner = Array.from(
    { length: steps + 1 },
    (_, i) => `L ${point(innerRadius, endDeg - (span * i) / steps)}`,
  );
  return `M ${point(outerRadius, startDeg)} ${outer.join(" ")} ${inner.join(" ")} Z`;
}

export function computeVenueLayout(seats: Seat[], layout: VenueLayoutKind): VenueLayout {
  const bySection = new Map<string, Seat[]>();
  for (const seat of seats) {
    if (!bySection.has(seat.section)) bySection.set(seat.section, []);
    bySection.get(seat.section)!.push(seat);
  }

  const rings: Ring[] = [];
  let longestNameLen = 1;
  for (const name of bySection.keys()) {
    const rows = groupRows(bySection.get(name)!);
    const maxRowLen = Math.max(...rows.map((r) => r.length), 1);
    const shape = sectionShape(name, layout);
    const last = rings[rings.length - 1];
    if (shape === "wedge" && last?.shape === "wedge") {
      last.sections.push({ name, rows, maxRowLen });
    } else {
      rings.push({ shape, sections: [{ name, rows, maxRowLen }] });
    }
    longestNameLen = Math.max(longestNameLen, name.length);
  }

  // Sized off the longest section name actually in this venue (see
  // labelHalfWidthPx) rather than a flat constant — a gap generous enough
  // for "Lower Bowl North" was needlessly huge for "Orchestra"/"Balcony",
  // pushing simple two-section theaters into unnecessary horizontal
  // scrolling to accommodate label lengths that don't apply to them.
  const labelHalfWidth = labelHalfWidthPx("x".repeat(longestNameLen));
  const RING_GAP_PX = Math.max(56, Math.round(2 * (labelHalfWidth + SEAT_BOX / 2)));
  const LABEL_MARGIN_PX = Math.round(RING_GAP_PX / 2);

  const fullCircle = layout === "stadium";
  // Theater half-span is kept under 90° so no seat's angle ever wraps
  // behind the stage line (cos of the extreme angle must stay positive) —
  // 200° let edge seats at large radius end up with negative y, poking
  // above the stage.
  const totalSpanDeg = fullCircle ? 360 : 160;
  const centerRadius = fullCircle ? 70 : 0;

  let radiusCursor = MIN_BASE_RADIUS + centerRadius;
  let maxRadius = radiusCursor;
  const sections: PositionedSection[] = [];

  for (const ring of rings) {
    const ringMaxRows = Math.max(...ring.sections.map((s) => s.rows.length), 1);

    if (ring.shape === "band") {
      const section = ring.sections[0];
      const n = section.maxRowLen;
      const anglePerSeatDeg = fullCircle ? totalSpanDeg / n : totalSpanDeg / Math.max(n - 1, 1);
      const requiredRadius = SEAT_PITCH_PX / (anglePerSeatDeg * DEG2RAD);
      const ringRadius = Math.max(radiusCursor, requiredRadius);

      const positioned = section.rows.flatMap((row, rowIndex) =>
        layOutRow(row, ringRadius + rowIndex * ROW_GAP_PX, 0, anglePerSeatDeg, fullCircle),
      );
      const outerRowRadius = ringRadius + (section.rows.length - 1) * ROW_GAP_PX;
      const labelRadius = outerRowRadius + LABEL_MARGIN_PX;
      const halfSpan = fullCircle ? 180 : totalSpanDeg / 2;
      sections.push({
        name: section.name,
        seats: positioned,
        labelX: 0,
        labelY: labelRadius,
        innerRadius: Math.max(ringRadius - ROW_GAP_PX / 2, centerRadius),
        outerRadius: outerRowRadius + ROW_GAP_PX / 2,
        startDeg: fullCircle ? 0 : -halfSpan,
        endDeg: fullCircle ? 360 : halfSpan,
      });

      maxRadius = Math.max(maxRadius, outerRowRadius);
      radiusCursor = ringRadius + (ringMaxRows - 1) * ROW_GAP_PX + RING_GAP_PX;
      continue;
    }

    // Wedge ring: give each section an angular slice proportional to its
    // row width (seats per row), not an equal split — a 14-seat-wide
    // grandstand needs more room than a 10-seat end zone to keep the same
    // physical seat pitch, and equal division was what made sections look
    // lopsided/asymmetric.
    //
    // The gap between adjacent sections needs the same physical width as
    // the gap between any two ordinary seats in a row — a flat
    // SECTION_GAP_DEG was too tight whenever a section's own per-seat
    // angle was wider than that (e.g. a 14-seats-per-row section spread
    // across ~100°: ~8° per seat, so a fixed 5° inter-section gap was
    // *narrower* than the gap between two ordinary seats, and edge seats
    // from neighboring sections visually collided). Size it in degrees the
    // same way seat pitch is sized, at the ring's own radius, with one
    // extra pass since the gap and the radius each depend on the other.
    const totalSeatWidth = ring.sections.reduce((sum, s) => sum + s.maxRowLen, 0) || 1;
    let gapDeg = SECTION_GAP_DEG;
    let usableTotalDeg = Math.max(totalSpanDeg - ring.sections.length * gapDeg, totalSpanDeg * 0.5);
    let anglePerSeatRad = (usableTotalDeg * DEG2RAD) / totalSeatWidth;
    let ringRadius = Math.max(radiusCursor, SEAT_PITCH_PX / anglePerSeatRad);

    gapDeg = Math.max(SECTION_GAP_DEG, (SEAT_PITCH_PX / ringRadius) * (180 / Math.PI));
    usableTotalDeg = Math.max(totalSpanDeg - ring.sections.length * gapDeg, totalSpanDeg * 0.5);
    anglePerSeatRad = (usableTotalDeg * DEG2RAD) / totalSeatWidth;
    ringRadius = Math.max(radiusCursor, SEAT_PITCH_PX / anglePerSeatRad);

    // widthDeg is the section's seat-occupying span only (usableTotalDeg
    // already excludes all N gaps); each section is placed flush against
    // the next with gapDeg added AFTER it, so all N boundaries — including
    // the one where the last section wraps back around to the first —
    // end up the same size. Trimming gapDeg out of each section's own slot
    // instead (the previous approach) only created a gap at internal
    // boundaries; the wrap-around boundary silently absorbed the rest of
    // the circle's leftover degrees and ended up several times wider.
    let cursorDeg = 0;
    for (const section of ring.sections) {
      const widthDeg = (section.maxRowLen / totalSeatWidth) * usableTotalDeg;
      const sectionCenterDeg = cursorDeg + widthDeg / 2;
      const anglePerSeatDeg = widthDeg / Math.max(section.maxRowLen - 1, 1);

      const positioned = section.rows.flatMap((row, rowIndex) =>
        layOutRow(row, ringRadius + rowIndex * ROW_GAP_PX, sectionCenterDeg, anglePerSeatDeg, false),
      );
      const outerRowRadius = ringRadius + (section.rows.length - 1) * ROW_GAP_PX;
      const labelRadius = outerRowRadius + LABEL_MARGIN_PX;
      const labelRad = sectionCenterDeg * DEG2RAD;
      sections.push({
        name: section.name,
        seats: positioned,
        labelX: labelRadius * Math.sin(labelRad),
        labelY: labelRadius * Math.cos(labelRad),
        innerRadius: Math.max(ringRadius - ROW_GAP_PX / 2, centerRadius),
        outerRadius: outerRowRadius + ROW_GAP_PX / 2,
        startDeg: cursorDeg,
        endDeg: cursorDeg + widthDeg,
      });

      maxRadius = Math.max(maxRadius, outerRowRadius);
      cursorDeg += widthDeg + gapDeg;
    }

    radiusCursor = ringRadius + (ringMaxRows - 1) * ROW_GAP_PX + RING_GAP_PX;
  }

  const halfSpanRad = (Math.min(totalSpanDeg, 180) / 2) * DEG2RAD;
  const extent = maxRadius + LABEL_MARGIN_PX + SEAT_BOX;
  const width = fullCircle ? extent * 2 : Math.sin(halfSpanRad) * extent * 2;
  const height = fullCircle ? extent * 2 : extent + 40;

  return {
    sections,
    width,
    height,
    centerX: width / 2,
    centerY: fullCircle ? height / 2 : 40,
    centerRadius,
    centerLabel: fullCircle ? "FIELD" : "STAGE",
  };
}
