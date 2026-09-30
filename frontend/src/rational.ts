// Exact rational quarter-length arithmetic on the client, matching the
// server (app/rational.py). Editor math never uses binary floats for time.

import type { Q } from "./types";

function gcd(a: number, b: number): number {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) [a, b] = [b, a % b];
  return a || 1;
}

export function q(n: number, d = 1): Q {
  const g = gcd(n, d);
  return { num: n / g, den: d / g };
}

export function qnum(a: Q): number {
  return a.num / a.den;
}

export function qadd(a: Q, b: Q): Q {
  return q(a.num * b.den + b.num * a.den, a.den * b.den);
}

export function qsub(a: Q, b: Q): Q {
  return q(a.num * b.den - b.num * a.den, a.den * b.den);
}

export function qcmp(a: Q, b: Q): number {
  return a.num * b.den - b.num * a.den;
}

export function qeq(a: Q | undefined, b: Q | undefined): boolean {
  if (!a || !b) return false;
  return qcmp(a, b) === 0;
}

export function qstr(a: Q): string {
  return a.den === 1 ? `${a.num}` : `${a.num}/${a.den}`;
}

// quarter-length -> beats display given a beat unit (4 = quarter beat)
export function beatLabel(time: Q, beatUnit: number): string {
  const perBeat = 4 / beatUnit;
  const v = qnum(time) / perBeat;
  return Number(v.toFixed(4)).toString();
}
