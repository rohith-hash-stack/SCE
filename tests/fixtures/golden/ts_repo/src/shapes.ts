export interface Shape {
  area(): number;
}

export class Rect implements Shape {
  constructor(public w: number, public h: number) {}

  area(): number {
    return this.w * this.h;
  }
}

export class Square extends Rect {
  constructor(side: number) {
    super(side, side);
  }

  override area(): number {
    return this.w ** 2;
  }
}

/** Total area over the interface type (virtual dispatch). */
export function totalArea(shapes: Shape[]): number {
  return shapes.reduce((acc, s) => acc + s.area(), 0);
}
