const clothHeight: number = 45,
  clothWidth: number = 50,
  springLength: number = 5,
  maxDistance: number = 35;

class Cloth {
  points: Point[];

  constructor() {
    this.points = [];

    const xBase: number = canvas.width / 2 - (clothWidth * springLength) / 2;
    const yBase: number = 20;

    for (let y: number = 0; y <= clothHeight; y++)
      for (let x: number = 0; x <= clothWidth; x++)
        this.makePoint(x, y, xBase, yBase);
  }

  private makePoint = (
    x: number,
    y: number,
    xBase: number,
    yBase: number
  ): void => {
    const p = new Point(xBase + x * springLength, yBase + y * springLength);

    x != 0 && p.attachPoint(this.points[this.points.length - 1]);
    y == 0 && p.pinDown(p.x, p.y);
    y != 0 && p.attachPoint(this.points[x + (y - 1) * (clothWidth + 1)]);

    this.points.push(p);
  };
  public update = (): void => {
    let i: number = physicsIteration;

    while (i--) this.points.forEach((p: Point) => p.resolveConstraints());

    this.points.forEach((p: Point) => p.update(0.016));
  };

  public draw = (): void => {
    ctx.beginPath();
    this.points.forEach((p: Point) => p.draw());
    ctx.stroke();
  };
}
