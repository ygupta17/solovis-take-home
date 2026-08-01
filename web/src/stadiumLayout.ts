import type { Seat, VenueLayoutKind } from "./types";

/**
 * Pure geometry: turns a flat seat list into polar (then cartesian) seat
 * positions arranged in curved rows around a central stage (theater — a
 * semicircle) or field (stadium — a full ring). No rendering here, just
 * numbers — SeatMap.tsx consumes this to position absolutely-placed seat
 * buttons. Kept separate from SeatMap so the trigonometry doesn't tangle
 * with interaction/state logic.
 *
 * Rows within a section are concentric arcs (front row = smallest radius,
 * closest to the stage/field); seats within a row are evenly spread across
 * the section's allotted angular span, using ONE angle-per-seat for every
 * row in the section (not scaled per-row), so back rows end up physically
 * wider than front rows — closer to how a real venue looks than uniformly
 * compressing outer rows would be.
 *
 * The tricky part is sizing: a fixed angle-per-seat only avoids seats
 * overlapping each other if the row's radius is large enough that the
 * physical arc length (radius × angle) is at least one seat-pitch wide.
 * The front row has the smallest radius, so it's always the tightest
 * constraint. This is computed in two passes: first work out how large the
 * base radius needs to be for every section's front row to fit its widest
 * row at a decent seat pitch, then lay everything out using that radius.
 * Skipping this (i.e. picking an arbitrary fixed radius) is what produced
 * the earlier bug — seats in the innermost rows overlapped each other so
 * badly the whole map looked like a tangled mess.
 */

const DEG2RAD = Math.PI / 180;
const SEAT_PITCH_PX = 26;
const SECTION_GAP_DEG = 5;
const ROW_GAP_PX = 32;
export const SEAT_BOX = 22;
const LABEL_MARGIN_PX = 24;
const MIN_BASE_RADIUS = 60;

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

export function computeVenueLayout(seats: Seat[], layout: VenueLayoutKind): VenueLayout {
  const bySection = new Map<string, Seat[]>();
  for (const seat of seats) {
    if (!bySection.has(seat.section)) bySection.set(seat.section, []);
    bySection.get(seat.section)!.push(seat);
  }
  const sectionNames = [...bySection.keys()];
  const sectionCount = Math.max(sectionNames.length, 1);

  const totalSpanDeg = layout === "stadium" ? 360 : 200;
  const centerRadius = layout === "stadium" ? 70 : 0;
  const allotmentDeg = totalSpanDeg / sectionCount;
  const usableDeg = Math.max(allotmentDeg - SECTION_GAP_DEG, allotmentDeg * 0.5);
  const usableRad = usableDeg * DEG2RAD;
  // Center the whole fan: for a full circle this is a no-op (mod 360); for
  // a semicircle it centers sections left/right around "straight ahead".
  const startOffset = layout === "stadium" ? 0 : -totalSpanDeg / 2;

  const sectionRows = sectionNames.map((name) => groupRows(bySection.get(name)!));
  const maxRowLens = sectionRows.map((rows) => Math.max(...rows.map((r) => r.length), 1));

  // Pass 1: find the radius the widest section actually needs so its front
  // row's seats sit at least SEAT_PITCH_PX apart — see module docstring.
  let baseRadius = MIN_BASE_RADIUS;
  maxRowLens.forEach((maxRowLen) => {
    if (maxRowLen <= 1) return;
    const anglePerSeatRad = usableRad / (maxRowLen - 1);
    const required = SEAT_PITCH_PX / anglePerSeatRad;
    baseRadius = Math.max(baseRadius, required);
  });

  // Pass 2: lay out every seat using that radius.
  let maxRadius = baseRadius + centerRadius;
  const sections: PositionedSection[] = sectionNames.map((name, i) => {
    const sectionCenterDeg = startOffset + allotmentDeg * (i + 0.5);
    const rows = sectionRows[i];
    const maxRowLen = maxRowLens[i];
    const anglePerSeatDeg = usableDeg / Math.max(maxRowLen - 1, 1);

    const positioned: PositionedSeat[] = [];
    rows.forEach((row, rowIndex) => {
      const radius = baseRadius + centerRadius + rowIndex * ROW_GAP_PX;
      maxRadius = Math.max(maxRadius, radius);
      const sorted = [...row].sort((a, b) => a.seat_number - b.seat_number);
      const n = sorted.length;
      sorted.forEach((seat, seatIndex) => {
        const relAngle = (seatIndex - (n - 1) / 2) * anglePerSeatDeg;
        const rad = (sectionCenterDeg + relAngle) * DEG2RAD;
        positioned.push({
          seat,
          x: radius * Math.sin(rad),
          y: radius * Math.cos(rad),
        });
      });
    });

    const labelRadius =
      baseRadius + centerRadius + (rows.length - 1) * ROW_GAP_PX + LABEL_MARGIN_PX;
    const labelRad = sectionCenterDeg * DEG2RAD;
    return {
      name,
      seats: positioned,
      labelX: labelRadius * Math.sin(labelRad),
      labelY: labelRadius * Math.cos(labelRad),
    };
  });

  const halfSpanRad = (Math.min(totalSpanDeg, 180) / 2) * DEG2RAD;
  const extent = maxRadius + LABEL_MARGIN_PX + SEAT_BOX;
  const width = layout === "stadium" ? extent * 2 : Math.sin(halfSpanRad) * extent * 2;
  const height = layout === "stadium" ? extent * 2 : extent + 40;

  return {
    sections,
    width,
    height,
    centerX: width / 2,
    centerY: layout === "stadium" ? height / 2 : 40,
    centerRadius,
    centerLabel: layout === "stadium" ? "FIELD" : "STAGE",
  };
}
