// Minimal exact rational arithmetic for musical time ("n/d" beat strings).
// The browser must never accumulate float drift on triplets, so all time math
// uses these Fractions and only converts to a number for geometry.

export class Q {
  constructor(public readonly num: number, public readonly den: number) {
    if (den === 0) throw new Error("zero denominator");
    const g = gcd(Math.abs(num), Math.abs(den)) || 1;
    this.num = (den < 0 ? -num : num) / g;
    this.den = Math.abs(den) / g;
  }

  static parse(s: string | number): Q {
    if (typeof s === "number") return Q.fromFloat(s);
    const t = String(s).trim();
    if (t.includes("/")) {
      const [n, d] = t.split("/");
      return new Q(parseInt(n, 10), parseInt(d, 10));
    }
    return new Q(parseInt(t, 10), 1);
  }

  static fromFloat(x: number): Q {
    // music21-style limit, beats are simple rationals
    const f = farey(x, 100000);
    return new Q(f[0], f[1]);
  }

  add(o: Q): Q {
    return new Q(this.num * o.den + o.num * this.den, this.den * o.den);
  }
  sub(o: Q): Q {
    return new Q(this.num * o.den - o.num * this.den, this.den * o.den);
  }
  mul(o: Q): Q {
    return new Q(this.num * o.num, this.den * o.den);
  }
  div(o: Q): Q {
    return new Q(this.num * o.den, this.den * o.num);
  }
  cmp(o: Q): number {
    return this.num * o.den - o.num * this.den;
  }
  get value(): number {
    return this.num / this.den;
  }
  toString(): string {
    return this.den === 1 ? `${this.num}` : `${this.num}/${this.den}`;
  }
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

function farey(x: number, maxD: number): [number, number] {
  if (Number.isInteger(x)) return [x, 1];
  let a = 0, b = 1, c = 1, d = 1;
  for (;;) {
    const m = a + c, n = b + d;
    if (n > maxD) return m / n > x ? [c, d] : [a, b];
    if (m / n > x) { c = m; d = n; } else { a = m; b = n; }
    if (m / n === x) return [m, n];
  }
}
